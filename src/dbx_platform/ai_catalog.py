"""Unified AI model catalog: models and the identities that can access them.

One catalog across the platform's AI surfaces:

* ``databricks_uc``      — Unity Catalog registered models + their grants
  (models are securable type FUNCTION; ``EXECUTE`` = can run inference).
* ``databricks_serving`` — model serving endpoints / served entities + their
  workspace ACLs.
* ``azure_openai``       — Azure AI Foundry / Azure OpenAI accounts and model
  deployments via Azure Resource Graph, plus the RBAC role assignments (direct
  and inherited) that grant access to them.

Azure auth is keyless: ``secrets.get_credential()`` resolves a Unity Catalog
service credential inside a Databricks runtime (or DefaultAzureCredential
locally). The identity needs the **Reader** role on every subscription passed
via ``--subscriptions``; leaving the list empty queries all subscriptions the
identity can read — see docs/cloud-setup.md (Azure Resource Graph access).
If a tenant does not surface deployments in Resource Graph,
``fetch_deployments_via_arm`` lists them per account through plain ARM.

Fetch/normalize/classify split as everywhere else: ``fetch_resource_graph``
and the two SDK wrappers are the only I/O; request-body construction, scope
matching, access-level mapping and SQL construction are pure and unit-tested
offline. Snapshots are reconciled per source with is_current/first_seen_at/
last_seen_at semantics — a disappeared model or grant is resolved, never
deleted, and an unavailable source never resolves its previous rows.
"""

from __future__ import annotations

import json
import re
import time

from databricks.sdk import WorkspaceClient

from dbx_platform.system_tables import run_query

_ARM_SCOPE = "https://management.azure.com/.default"
_ARG_URL = (
    "https://management.azure.com/providers/"
    "Microsoft.ResourceGraph/resources?api-version=2022-10-01"
)
_ARM_DEPLOYMENTS_API = "2025-06-01"
_MAX_RETRIES = 5

SOURCES = ("databricks_uc", "databricks_serving", "azure_openai")

# Which findings key is evidenced by which source. The sync command drops the
# keys of sources that failed to refresh so store_findings cannot resolve
# findings on stale evidence.
CHECK_SOURCES: dict[str, tuple[str, ...]] = {
    "ai-catalog/azure-key-auth": ("azure_openai",),
    "ai-catalog/azure-broad-scope-role": ("azure_openai",),
    "ai-catalog/uc-model-broad-grant": ("databricks_uc",),
    "ai-catalog/endpoint-open-acl": ("databricks_serving",),
    # One canonical check spans the two endpoint/workspace inventories.  It
    # is only reconciled when both sources refreshed, so an outage in either
    # source can never resolve a previously observed ZDR failure.
    "ai-catalog/zdr-disabled": ("databricks_serving", "azure_openai"),
}


def check_sources_refreshed(check: str, refreshed: list[str]) -> bool:
    """Whether every source required to reconcile ``check`` refreshed."""

    return all(source in refreshed for source in CHECK_SOURCES[check])

# Columns of the two tables; the normalizers emit dicts with exactly these
# keys (workspace/environment/timestamps are bound at store time).
CATALOG_ROW_SCHEMA = (
    "array<struct<source:string,model_key:string,provider:string,model_name:string,"
    "model_version:string,entity_type:string,endpoint_name:string,status:string,"
    "region:string,subscription_id:string,resource_group:string,resource_id:string,"
    "owner:string,key_auth_enabled:boolean,usage_tracking:boolean,details_json:string>>"
)

ACCESS_ROW_SCHEMA = (
    "array<struct<source:string,model_key:string,principal_id:string,"
    "principal_name:string,principal_type:string,access_level:string,"
    "role_or_privilege:string,scope:string,via:string,details_json:string>>"
)

# --- Azure Resource Graph (Kusto) ----------------------------------------------

AI_ACCOUNTS_QUERY = """
resources
| where type =~ 'microsoft.cognitiveservices/accounts'
| where kind in~ ('OpenAI', 'AIServices')
| project id = tolower(id), name, location, kind, subscriptionId, resourceGroup,
    skuName = tostring(sku.name),
    endpoint = tostring(properties.endpoint),
    disableLocalAuth = tobool(properties.disableLocalAuth),
    publicNetworkAccess = tostring(properties.publicNetworkAccess),
    tags
"""

AI_DEPLOYMENTS_QUERY = """
resources
| where type =~ 'microsoft.cognitiveservices/accounts/deployments'
| project id = tolower(id), name, subscriptionId, resourceGroup,
    modelName = tostring(properties.model.name),
    modelVersion = tostring(properties.model.version),
    modelFormat = tostring(properties.model.format),
    skuName = tostring(sku.name), skuCapacity = toint(sku.capacity),
    provisioningState = tostring(properties.provisioningState)
"""

AI_ROLE_ASSIGNMENTS_QUERY = """
authorizationresources
| where type =~ 'microsoft.authorization/roleassignments'
| extend scope = tolower(tostring(properties.scope)),
    principalId = tostring(properties.principalId),
    principalType = tostring(properties.principalType),
    roleDefinitionId = tolower(tostring(properties.roleDefinitionId))
| where scope contains 'microsoft.cognitiveservices/'
    or scope matches regex '^/subscriptions/[0-9a-f-]+$'
    or scope matches regex '^/subscriptions/[0-9a-f-]+/resourcegroups/[^/]+$'
    or scope startswith '/providers/microsoft.management/managementgroups/'
| join kind=leftouter (
    authorizationresources
    | where type =~ 'microsoft.authorization/roledefinitions'
    | project roleDefinitionId = tolower(id),
        roleName = tostring(properties.roleName),
        roleType = tostring(properties.type)
  ) on roleDefinitionId
| project id = tolower(id), scope, principalId, principalType,
    roleDefinitionId, roleName, roleType
"""


def parse_subscriptions(raw: str | None) -> list[str]:
    """Split a comma-separated subscription-ID flag value. Pure."""
    if not raw:
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def build_resource_graph_body(
    query: str,
    subscriptions: list[str] | None,
    *,
    skip_token: str | None = None,
    authorization_scoped: bool = False,
) -> dict:
    """Request body for one Resource Graph page. Pure.

    A non-empty ``subscriptions`` list scopes the query (least-privilege
    Reader per subscription); omitted, ARG spans every subscription the
    identity can read. ``authorization_scoped`` widens the scope filter so
    role assignments inherited from management groups are still returned
    when the query is subscription-scoped.
    """
    options: dict = {"resultFormat": "objectArray"}
    body: dict = {"query": query, "options": options}
    if subscriptions:
        body["subscriptions"] = list(subscriptions)
        if authorization_scoped:
            options["authorizationScopeFilter"] = "AtScopeAboveAndBelow"
    if skip_token:
        options["$skipToken"] = skip_token
    return body


def fetch_resource_graph(
    credential,
    query: str,
    subscriptions: list[str] | None = None,
    *,
    authorization_scoped: bool = False,
) -> list[dict]:
    """Run one Kusto query against Azure Resource Graph; returns result rows.

    ``credential`` is azure-identity-compatible (see secrets.get_credential).
    Follows $skipToken paging and honors 429 Retry-After.
    """
    import requests  # ships with databricks-sdk; keep the core wheel lean

    token = credential.get_token(_ARM_SCOPE).token
    rows: list[dict] = []
    skip_token: str | None = None
    retries = 0
    while True:
        body = build_resource_graph_body(
            query,
            subscriptions,
            skip_token=skip_token,
            authorization_scoped=authorization_scoped,
        )
        resp = requests.post(
            _ARG_URL,
            json=body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
        if resp.status_code == 429 and retries < _MAX_RETRIES:
            retries += 1
            time.sleep(int(resp.headers.get("Retry-After", "15")))
            continue
        if resp.status_code == 403:
            raise RuntimeError(
                "Azure Resource Graph returned 403. The identity needs the "
                "'Reader' role on every subscription in --subscriptions (or "
                "on a management group for the all-visible mode) — see "
                "docs/cloud-setup.md (Azure Resource Graph access)."
            )
        resp.raise_for_status()
        payload = resp.json()
        rows.extend(payload.get("data") or [])
        skip_token = payload.get("$skipToken")
        if not skip_token:
            return rows


def fetch_deployments_via_arm(credential, account_ids: list[str]) -> list[dict]:
    """Per-account ARM fallback when this tenant's ARG omits deployments.

    Emits rows shaped exactly like AI_DEPLOYMENTS_QUERY results.
    """
    import requests

    token = credential.get_token(_ARM_SCOPE).token
    rows: list[dict] = []
    for account_id in account_ids:
        url = (
            f"https://management.azure.com{account_id}/deployments"
            f"?api-version={_ARM_DEPLOYMENTS_API}"
        )
        retries = 0
        while url:
            resp = requests.get(
                url, headers={"Authorization": f"Bearer {token}"}, timeout=60
            )
            if resp.status_code == 429 and retries < _MAX_RETRIES:
                retries += 1
                time.sleep(int(resp.headers.get("Retry-After", "15")))
                continue
            resp.raise_for_status()
            payload = resp.json()
            for item in payload.get("value") or []:
                props = item.get("properties") or {}
                model = props.get("model") or {}
                sku = item.get("sku") or {}
                dep_id = str(item.get("id") or "").lower()
                rows.append(
                    {
                        "id": dep_id or f"{account_id}/deployments/{item.get('name', '')}",
                        "name": str(item.get("name") or ""),
                        "subscriptionId": _subscription_of(account_id),
                        "resourceGroup": _resource_group_of(account_id),
                        "modelName": str(model.get("name") or ""),
                        "modelVersion": str(model.get("version") or ""),
                        "modelFormat": str(model.get("format") or ""),
                        "skuName": str(sku.get("name") or ""),
                        "skuCapacity": sku.get("capacity"),
                        "provisioningState": str(props.get("provisioningState") or ""),
                    }
                )
            url = payload.get("nextLink")
    return rows


# --- Databricks fetches ---------------------------------------------------------

def fetch_model_grants(
    w: WorkspaceClient, full_names: list[str]
) -> tuple[list[dict], int]:
    """UC grants per registered model. Returns (grants, error_count).

    Registered models are securable type FUNCTION in the Grants API. One
    unreadable model degrades the source to partial instead of failing the
    sync.
    """
    from databricks.sdk.service.catalog import SecurableType

    grants: list[dict] = []
    errors = 0
    for full_name in full_names:
        try:
            resp = w.grants.get(SecurableType.FUNCTION, full_name)
        except Exception:  # noqa: BLE001 - one bad model must not kill the sync
            errors += 1
            continue
        for assignment in resp.privilege_assignments or []:
            privileges = [
                p.value if hasattr(p, "value") else str(p)
                for p in assignment.privileges or []
            ]
            grants.append(
                {
                    "full_name": full_name,
                    "principal": assignment.principal or "",
                    "privileges": privileges,
                }
            )
    return grants, errors


def fetch_endpoint_acls(
    w: WorkspaceClient, endpoints: list[dict]
) -> tuple[list[dict], int]:
    """Workspace ACLs per (non-system) serving endpoint.

    Returns (acls, error_count); each ACL row carries the per-level
    ``inherited`` flag from the permissions API.
    """
    acls: list[dict] = []
    errors = 0
    for endpoint in endpoints:
        if endpoint.get("is_system_endpoint") or not endpoint.get("endpoint_id"):
            continue
        try:
            perms = w.serving_endpoints.get_permissions(
                serving_endpoint_id=endpoint["endpoint_id"]
            )
        except Exception:  # noqa: BLE001 - one bad endpoint must not kill the sync
            errors += 1
            continue
        for entry in perms.access_control_list or []:
            principal = (
                entry.user_name or entry.group_name or entry.service_principal_name or ""
            )
            principal_type = (
                "USER" if entry.user_name
                else "GROUP" if entry.group_name
                else "SERVICE_PRINCIPAL"
            )
            levels = [
                {
                    "level": (
                        p.permission_level.value
                        if p.permission_level is not None
                        else ""
                    ),
                    "inherited": bool(p.inherited),
                }
                for p in entry.all_permissions or []
            ]
            acls.append(
                {
                    "endpoint_name": endpoint.get("name", ""),
                    "principal": principal,
                    "principal_type": principal_type,
                    "levels": levels,
                }
            )
    return acls, errors


# --- pure normalizers -----------------------------------------------------------

def _details(payload: dict) -> str:
    return json.dumps(payload, default=str, sort_keys=True)


def _subscription_of(resource_id: str) -> str:
    match = re.search(r"/subscriptions/([^/]+)", resource_id.lower())
    return match.group(1) if match else ""


def _resource_group_of(resource_id: str) -> str:
    match = re.search(r"/resourcegroups/([^/]+)", resource_id.lower())
    return match.group(1) if match else ""


def deployment_account_id(deployment_id: str) -> str:
    """Parent account resource ID of a deployment resource ID. Pure."""
    return deployment_id.lower().split("/deployments/")[0]


def normalize_azure_accounts(accounts: list[dict]) -> list[dict]:
    """Azure AI accounts as catalog rows (the container resources)."""
    rows = []
    for a in accounts:
        account_id = str(a.get("id") or "").lower()
        rows.append(
            {
                "source": "azure_openai",
                "model_key": f"azure:{account_id}",
                "provider": str(a.get("kind") or ""),
                "model_name": "",
                "model_version": "",
                "entity_type": "ACCOUNT",
                "endpoint_name": str(a.get("name") or ""),
                "status": str(a.get("publicNetworkAccess") or ""),
                "region": str(a.get("location") or ""),
                "subscription_id": str(a.get("subscriptionId") or ""),
                "resource_group": str(a.get("resourceGroup") or "").lower(),
                "resource_id": account_id,
                "owner": "",
                # disableLocalAuth False/None means API keys work: access that
                # RBAC cannot attribute to an identity.
                "key_auth_enabled": not bool(a.get("disableLocalAuth")),
                "usage_tracking": False,
                "details_json": _details(
                    {
                        "endpoint": a.get("endpoint") or "",
                        "sku": a.get("skuName") or "",
                        "tags": a.get("tags") or {},
                    }
                ),
            }
        )
    return rows


def normalize_azure_deployments(
    deployments: list[dict], accounts_by_id: dict[str, dict]
) -> list[dict]:
    """Azure model deployments as catalog rows; inherit account key-auth."""
    rows = []
    for d in deployments:
        deployment_id = str(d.get("id") or "").lower()
        account_id = deployment_account_id(deployment_id)
        account = accounts_by_id.get(account_id, {})
        rows.append(
            {
                "source": "azure_openai",
                "model_key": f"azure:{deployment_id}",
                "provider": str(d.get("modelFormat") or account.get("kind") or ""),
                "model_name": str(d.get("modelName") or ""),
                "model_version": str(d.get("modelVersion") or ""),
                "entity_type": "DEPLOYMENT",
                "endpoint_name": str(account.get("name") or account_id.rsplit("/", 1)[-1]),
                "status": str(d.get("provisioningState") or ""),
                "region": str(account.get("location") or ""),
                "subscription_id": str(
                    d.get("subscriptionId") or _subscription_of(deployment_id)
                ),
                "resource_group": str(
                    d.get("resourceGroup") or _resource_group_of(deployment_id)
                ).lower(),
                "resource_id": deployment_id,
                "owner": "",
                "key_auth_enabled": not bool(account.get("disableLocalAuth")),
                "usage_tracking": False,
                "details_json": _details(
                    {
                        "deployment": d.get("name") or "",
                        "sku": d.get("skuName") or "",
                        "capacity": d.get("skuCapacity"),
                        # Governance attestations are account-scoped tags and
                        # must follow each deployment into the catalog snapshot.
                        "account_tags": account.get("tags") or {},
                    }
                ),
            }
        )
    return rows


def normalize_registered_models(models: list[dict]) -> list[dict]:
    """UC registered models (ml.fetch_registered_models output) as catalog rows."""
    rows = []
    for m in models:
        versions = m.get("versions") or []
        newest = max((int(v.get("version") or 0) for v in versions), default=0)
        rows.append(
            {
                "source": "databricks_uc",
                "model_key": f"uc:{m['full_name']}",
                "provider": "databricks",
                "model_name": m["full_name"],
                "model_version": str(newest) if newest else "",
                "entity_type": "REGISTERED_MODEL",
                "endpoint_name": "",
                "status": "",
                "region": "",
                "subscription_id": "",
                "resource_group": "",
                "resource_id": m["full_name"],
                "owner": str(m.get("owner") or ""),
                "key_auth_enabled": False,
                "usage_tracking": False,
                "details_json": _details(
                    {
                        "aliases": m.get("aliases") or [],
                        "version_count": len(versions),
                    }
                ),
            }
        )
    return rows


def normalize_serving_entities(endpoints: list[dict]) -> list[dict]:
    """Served entities (ml.fetch_serving_endpoints output) as catalog rows."""
    rows = []
    for e in endpoints:
        if e.get("is_system_endpoint"):
            continue
        for se in e.get("served_entities", []):
            entity_name = se.get("entity_name") or ""
            rows.append(
                {
                    "source": "databricks_serving",
                    "model_key": f"serving:{e['name']}/{entity_name}",
                    "provider": "databricks",
                    "model_name": entity_name,
                    "model_version": str(se.get("entity_version") or ""),
                    "entity_type": (
                        "EXTERNAL_OR_FOUNDATION_MODEL"
                        if se.get("is_external_or_fm")
                        else "CUSTOM_MODEL"
                    ),
                    "endpoint_name": e["name"],
                    "status": str(e.get("ready") or ""),
                    "region": "",
                    "subscription_id": "",
                    "resource_group": "",
                    "resource_id": f"{e['name']}/{entity_name}",
                    "owner": str(e.get("creator") or ""),
                    "key_auth_enabled": False,
                    "usage_tracking": bool(e.get("has_usage_tracking")),
                    "details_json": _details(
                        {
                            "task": e.get("task") or "",
                            "workload_size": se.get("workload_size") or "",
                            "scale_to_zero": bool(se.get("scale_to_zero")),
                            "has_rate_limits": bool(e.get("has_rate_limits")),
                            "content_safety_enabled": e.get(
                                "content_safety_enabled"
                            ),
                            # Preserve explicit endpoint governance tags.  In
                            # particular, ZDR is never guessed from provider or
                            # workload type: a missing tag remains unverified.
                            "tags": e.get("tags") or {},
                        }
                    ),
                }
            )
    return rows


_ADMIN_ROLES = {
    "owner",
    "contributor",
    "cognitive services contributor",
    "cognitive services openai contributor",
    "azure ai administrator",
    "foundry account owner",
    "foundry owner",
}
_INVOKE_ROLES = {
    "cognitive services openai user",
    "cognitive services user",
    "azure ai user",
    "azure ai developer",
    "foundry user",
}


def azure_access_level(role_name: str) -> str:
    """Map an Azure role name onto ADMIN / INVOKE / READ / OTHER. Pure."""
    name = (role_name or "").strip().lower()
    if name in _ADMIN_ROLES:
        return "ADMIN"
    if name in _INVOKE_ROLES:
        return "INVOKE"
    if "reader" in name:
        return "READ"
    return "OTHER"


def match_assignments_to_accounts(
    assignments: list[dict], accounts: list[dict]
) -> list[dict]:
    """Join role assignments to the AI accounts they cover. Pure.

    Direct (resource or child scope), resource-group and subscription scopes
    are matched exactly; management-group assignments cannot be resolved to a
    subscription set without extra queries, so they conservatively apply to
    every account. Unrelated roles (access_level OTHER) are kept only when
    assigned directly on an AI resource.
    """
    rows: list[dict] = []

    def emit(assignment: dict, account: dict, via: str) -> None:
        role = str(
            assignment.get("roleName") or assignment.get("roleDefinitionId") or ""
        )
        level = azure_access_level(role)
        scope = str(assignment.get("scope") or "").lower()
        if level == "OTHER" and "microsoft.cognitiveservices/" not in scope:
            return
        account_id = str(account.get("id") or "").lower()
        rows.append(
            {
                "source": "azure_openai",
                "model_key": f"azure:{account_id}",
                "principal_id": str(assignment.get("principalId") or ""),
                "principal_name": str(assignment.get("principalId") or ""),
                "principal_type": str(assignment.get("principalType") or ""),
                "access_level": level,
                "role_or_privilege": role,
                "scope": scope,
                "via": via,
                "details_json": _details(
                    {
                        "assignment_id": assignment.get("id") or "",
                        "role_type": assignment.get("roleType") or "",
                    }
                ),
            }
        )

    for assignment in assignments:
        scope = str(assignment.get("scope") or "").lower()
        if scope.startswith("/providers/microsoft.management/managementgroups/"):
            for account in accounts:
                emit(assignment, account, "MANAGEMENT_GROUP")
            continue
        for account in accounts:
            account_id = str(account.get("id") or "").lower()
            sub = str(account.get("subscriptionId") or "").lower()
            rg = str(account.get("resourceGroup") or "").lower()
            if scope == account_id or scope.startswith(account_id + "/"):
                emit(assignment, account, "DIRECT")
            elif scope == f"/subscriptions/{sub}":
                emit(assignment, account, "SUBSCRIPTION")
            elif scope == f"/subscriptions/{sub}/resourcegroups/{rg}":
                emit(assignment, account, "RESOURCE_GROUP")
    return rows


def normalize_uc_grants(grants: list[dict]) -> list[dict]:
    """UC model grants (fetch_model_grants output) as access rows."""
    rows = []
    for g in grants:
        privileges = [str(p).upper() for p in g.get("privileges") or []]
        level = (
            "INVOKE"
            if "EXECUTE" in privileges or "ALL_PRIVILEGES" in privileges
            else "OTHER"
        )
        rows.append(
            {
                "source": "databricks_uc",
                "model_key": f"uc:{g['full_name']}",
                "principal_id": str(g.get("principal") or ""),
                "principal_name": str(g.get("principal") or ""),
                "principal_type": "",
                "access_level": level,
                "role_or_privilege": ",".join(sorted(privileges)),
                "scope": g["full_name"],
                "via": "DIRECT",
                "details_json": _details({"privileges": sorted(privileges)}),
            }
        )
    return rows


_ENDPOINT_LEVEL_ORDER = ("CAN_MANAGE", "CAN_QUERY", "CAN_VIEW")


def normalize_endpoint_acls(acls: list[dict]) -> list[dict]:
    """Serving endpoint ACLs (fetch_endpoint_acls output) as access rows."""
    rows = []
    for acl in acls:
        levels = acl.get("levels") or []
        by_level = {entry.get("level"): entry for entry in levels}
        top = next((lv for lv in _ENDPOINT_LEVEL_ORDER if lv in by_level), None)
        if not top:
            continue
        rows.append(
            {
                "source": "databricks_serving",
                "model_key": f"serving:{acl['endpoint_name']}",
                "principal_id": str(acl.get("principal") or ""),
                "principal_name": str(acl.get("principal") or ""),
                "principal_type": str(acl.get("principal_type") or ""),
                "access_level": top,
                "role_or_privilege": top,
                "scope": acl["endpoint_name"],
                "via": "INHERITED" if by_level[top].get("inherited") else "DIRECT",
                "details_json": _details(
                    {"levels": sorted(str(e.get("level")) for e in levels)}
                ),
            }
        )
    return rows


# --- compliance posture (pure) -------------------------------------------------

_AI_COMPLIANCE_TYPES = {
    "ACCOUNT",
    "DEPLOYMENT",
    "CUSTOM_MODEL",
    "EXTERNAL_OR_FOUNDATION_MODEL",
}
_COMPLIANCE_BROAD_PRINCIPALS = {
    "account users",
    "all account users",
    "all users",
    "users",
}
_COMPLIANCE_BROAD_LEVELS = {"ADMIN", "INVOKE", "CAN_MANAGE", "CAN_QUERY"}
_ZDR_KEYS = (
    "zdr",
    "zdr_enabled",
    "zero_data_retention",
    "zero_data_retention_enabled",
)


def _bool_attestation(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on", "enabled", "enforced", "zdr"}:
            return True
        if normalized in {"false", "0", "no", "off", "disabled", "not_enforced"}:
            return False
    return None


def _parsed_details(row: dict) -> dict:
    value = row.get("details_json")
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _attestation_lookup(row: dict, *keys: str) -> object | None:
    """Read normalized fields and explicitly persisted governance metadata."""

    detail = _parsed_details(row)
    sources = [row, detail]
    for nested_key in ("tags", "account_tags", "governance"):
        nested = detail.get(nested_key)
        if isinstance(nested, dict):
            sources.append(nested)
    lowered_keys = {key.lower() for key in keys}
    for source in sources:
        for key, value in source.items():
            if str(key).lower() in lowered_keys and value not in (None, ""):
                return value
    return None


def _is_current(row: dict) -> bool:
    value = row.get("is_current", True)
    parsed = _bool_attestation(value)
    return True if parsed is None else parsed


def zdr_attestation(row: dict) -> bool | None:
    """Return only explicit ZDR evidence; missing evidence remains ``None``."""

    return _bool_attestation(_attestation_lookup(row, *_ZDR_KEYS))


def _compliance_resources(catalog_rows: list[dict]) -> list[dict]:
    """Current endpoint/workspace resources, de-duplicated at control scope."""

    resources: dict[str, dict] = {}
    for row in catalog_rows:
        if not _is_current(row):
            continue
        if str(row.get("entity_type") or "") not in _AI_COMPLIANCE_TYPES:
            continue
        if row.get("source") == "databricks_serving":
            endpoint = str(row.get("endpoint_name") or row.get("resource_id") or "unknown")
            key = f"databricks_serving:{endpoint}"
        else:
            key = f"{row.get('source')}:{row.get('model_key') or row.get('resource_id')}"
        resources.setdefault(key, row)
    return list(resources.values())


def _compliance_metric(
    metric_id: str,
    label: str,
    results: list[bool],
    total: int,
    evidence_note: str,
    *,
    score: float | None = None,
) -> dict:
    compliant = sum(1 for result in results if result)
    value = score
    if value is None and results:
        value = round(compliant / len(results) * 100, 1)
    return {
        "id": metric_id,
        "label": label,
        "value_pct": value,
        "compliant_resources": compliant,
        "evaluated_resources": len(results),
        "total_resources": total,
        "evidence_note": evidence_note,
    }


def build_compliance_posture(catalog_rows: list[dict], access_rows: list[dict]) -> dict:
    """Aggregate conservative cross-provider AI control posture.

    This package function is shared by the scheduled catalog classifier, API,
    digest, and assistant evidence path.  A missing attestation is reported as
    unknown and never silently contributes to a passing score.
    """

    resources = _compliance_resources(catalog_rows)
    current_access = [row for row in access_rows if _is_current(row)]
    total = len(resources)
    zdr_results: list[bool] = []
    content_safety_results: list[bool] = []
    access_results: list[bool] = []
    audit_results: list[bool] = []
    rate_headroom: list[float] = []
    alerts: list[dict] = []

    for row in resources:
        zdr = zdr_attestation(row)
        if zdr is not None:
            zdr_results.append(zdr)
            if not zdr:
                scope = "workspace" if row.get("entity_type") == "ACCOUNT" else "endpoint"
                resource_id = str(row.get("resource_id") or row.get("model_key") or "unknown")
                resource_name = str(
                    row.get("endpoint_name")
                    or row.get("model_name")
                    or resource_id.rsplit("/", 1)[-1]
                )
                alerts.append(
                    {
                        "resource_id": resource_id,
                        "resource_name": resource_name,
                        "scope": scope,
                        "provider": str(row.get("provider") or row.get("source") or "unknown"),
                        "status": "disabled",
                        "remediation": (
                            "Move traffic to an attested ZDR deployment, submit the provider "
                            "ZDR enablement request, and rerun the catalog sync before an "
                            "approved rollout."
                        ),
                    }
                )

        content_safety = _bool_attestation(
            _attestation_lookup(
                row,
                "content_safety",
                "content_safety_enabled",
                "rai_policy_enabled",
            )
        )
        if content_safety is None:
            policy_name = _attestation_lookup(
                row, "rai_policy_name", "content_filter_policy"
            )
            if policy_name not in (None, ""):
                content_safety = True
        if content_safety is not None:
            content_safety_results.append(content_safety)

        model_key = str(row.get("model_key") or "")
        related_access = [
            access
            for access in current_access
            if model_key == str(access.get("model_key") or "")
            or model_key.startswith(f"{access.get('model_key')}/")
        ]
        broad_access = any(
            str(access.get("principal_name") or access.get("principal_id") or "")
            .strip()
            .lower()
            in _COMPLIANCE_BROAD_PRINCIPALS
            and str(access.get("access_level") or "").upper()
            in _COMPLIANCE_BROAD_LEVELS
            for access in related_access
        )
        key_auth = _bool_attestation(row.get("key_auth_enabled"))
        if key_auth is not None or related_access:
            access_results.append(not bool(key_auth) and not broad_access)

        audit_logging = _bool_attestation(
            _attestation_lookup(row, "audit_logging", "audit_logging_enabled")
        )
        if audit_logging is None and row.get("source") == "databricks_serving":
            audit_logging = _bool_attestation(row.get("usage_tracking"))
        if audit_logging is not None:
            audit_results.append(audit_logging)

        headroom_value = _attestation_lookup(
            row,
            "rate_limit_headroom_pct",
            "rate_limit_headroom_percent",
            "rate_limit_headroom",
        )
        try:
            numeric_headroom = float(headroom_value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            numeric_headroom = None
        if numeric_headroom is not None:
            rate_headroom.append(max(0.0, min(100.0, numeric_headroom)))

    rate_score = (
        round(sum(rate_headroom) / len(rate_headroom), 1) if rate_headroom else None
    )
    rate_results = [value >= 20 for value in rate_headroom]
    return {
        "metrics": [
            _compliance_metric(
                "zdr",
                "ZDR Enforced Ratio",
                zdr_results,
                total,
                "Explicit ZDR resource attestations; missing evidence stays unverified.",
            ),
            _compliance_metric(
                "content_safety",
                "Content Safety Mitigation",
                content_safety_results,
                total,
                "Explicit content-filter or RAI policy attestations.",
            ),
            _compliance_metric(
                "access_control",
                "Access Control Consistency",
                access_results,
                total,
                "Keyless access and absence of broad invoke/admin grants.",
            ),
            _compliance_metric(
                "audit_logging",
                "Audit Logging",
                audit_results,
                total,
                "Usage tracking or explicit audit-log enablement.",
            ),
            _compliance_metric(
                "rate_limit_headroom",
                "Rate Limit Headroom",
                rate_results,
                total,
                "Average attested capacity remaining; 20% is the healthy floor.",
                score=rate_score,
            ),
        ],
        "zdr_alerts": alerts,
        "unverified_zdr_resources": max(0, total - len(zdr_results)),
        "evaluated_resources": total,
    }


# --- findings (pure) ------------------------------------------------------------

_BROAD_GROUPS = {"account users", "users", "all users", "all account users"}


def classify_ai_catalog(
    catalog_rows: list[dict], access_rows: list[dict]
) -> dict[str, list[dict]]:
    """Pure decision logic over catalog + access snapshots.

    Callers pass either freshly normalized rows (sync) or the persisted
    is_current rows (digest); rows without an is_current key count as
    current. Emits dicts keyed 'area/check' — the caller must drop keys whose
    evidencing source did not refresh (see CHECK_SOURCES).
    """
    findings: dict[str, list[dict]] = {key: [] for key in CHECK_SOURCES}

    def current(row: dict) -> bool:
        value = row.get("is_current", True)
        return bool(value) and str(value).lower() != "false"

    for row in catalog_rows:
        if not current(row):
            continue
        key_auth = row.get("key_auth_enabled")
        is_key_auth = bool(key_auth) and str(key_auth).lower() != "false"
        if (
            row.get("source") == "azure_openai"
            and row.get("entity_type") == "ACCOUNT"
            and is_key_auth
        ):
            findings["ai-catalog/azure-key-auth"].append(
                {
                    "name": row.get("endpoint_name") or row.get("resource_id", ""),
                    "resource_id": row.get("resource_id", ""),
                    "resource_type": "AI_ACCOUNT",
                    "subscription_id": row.get("subscription_id", ""),
                    "reason": "API-key auth is enabled (disableLocalAuth=false) — "
                              "model access cannot be attributed to an identity",
                    "action": "disable-key-auth (manual)",
                    "severity": "HIGH",
                }
            )

    for row in _compliance_resources(catalog_rows):
        if zdr_attestation(row) is not False:
            continue
        resource_id = str(row.get("resource_id") or row.get("model_key") or "unknown")
        resource_name = str(
            row.get("endpoint_name")
            or row.get("model_name")
            or resource_id.rsplit("/", 1)[-1]
        )
        scope = "workspace" if row.get("entity_type") == "ACCOUNT" else "endpoint"
        findings["ai-catalog/zdr-disabled"].append(
            {
                "name": resource_name,
                "resource_id": resource_id,
                "resource_type": "AI_WORKSPACE" if scope == "workspace" else "AI_ENDPOINT",
                "reason": (
                    f"Zero Data Retention is explicitly disabled for this {scope}; "
                    "request content may be retained by the serving provider"
                ),
                "action": "enable-zdr (manual)",
                "severity": "CRITICAL",
            }
        )

    seen_assignments: set[str] = set()
    for row in access_rows:
        if not current(row):
            continue
        source = row.get("source", "")
        principal = str(row.get("principal_name") or row.get("principal_id") or "")
        level = str(row.get("access_level") or "")
        if source == "azure_openai":
            if row.get("via") not in ("SUBSCRIPTION", "MANAGEMENT_GROUP"):
                continue
            if level not in ("INVOKE", "ADMIN"):
                continue
            try:
                details = json.loads(str(row.get("details_json") or "{}"))
            except json.JSONDecodeError:
                details = {}
            assignment_id = str(details.get("assignment_id") or "") or (
                f"{principal}:{row.get('role_or_privilege', '')}:{row.get('scope', '')}"
            )
            if assignment_id in seen_assignments:
                continue
            seen_assignments.add(assignment_id)
            findings["ai-catalog/azure-broad-scope-role"].append(
                {
                    "name": principal,
                    "resource_id": assignment_id,
                    "resource_type": "ROLE_ASSIGNMENT",
                    "reason": f"role '{row.get('role_or_privilege', '')}' grants "
                              f"{level} on AI resources at "
                              f"{str(row.get('via', '')).lower().replace('_', ' ')} "
                              f"scope {row.get('scope', '')}",
                    "action": "narrow-role-scope (manual)",
                    "severity": "MEDIUM",
                }
            )
        elif source == "databricks_uc":
            if principal.strip().lower() in _BROAD_GROUPS and level == "INVOKE":
                findings["ai-catalog/uc-model-broad-grant"].append(
                    {
                        "name": str(row.get("scope") or row.get("model_key", "")),
                        "resource_id": str(row.get("scope") or ""),
                        "resource_type": "MODEL",
                        "reason": f"'{principal}' holds "
                                  f"{row.get('role_or_privilege', 'EXECUTE')} — every "
                                  "account user can run inference on this model",
                        "action": "review-model-grant (manual)",
                        "severity": "HIGH",
                    }
                )
        elif source == "databricks_serving":
            if (
                principal.strip().lower() in _BROAD_GROUPS
                and level in ("CAN_QUERY", "CAN_MANAGE")
            ):
                findings["ai-catalog/endpoint-open-acl"].append(
                    {
                        "name": str(row.get("scope") or ""),
                        "resource_id": str(row.get("scope") or ""),
                        "resource_type": "SERVING_ENDPOINT",
                        "reason": f"'{principal}' holds {level} — every workspace "
                                  "user can query this endpoint",
                        "action": "review-endpoint-acl (manual)",
                        "severity": "HIGH",
                    }
                )
    return findings


# --- storage (Delta via the SQL warehouse) --------------------------------------

def create_catalog_table_sql(catalog: str, schema: str) -> str:
    """DDL for the ai_model_catalog table. Pure."""
    return (
        f"CREATE TABLE IF NOT EXISTS {catalog}.{schema}.ai_model_catalog ("
        "workspace_id STRING, environment STRING, source STRING, model_key STRING, "
        "provider STRING, model_name STRING, model_version STRING, "
        "entity_type STRING, endpoint_name STRING, status STRING, region STRING, "
        "subscription_id STRING, resource_group STRING, resource_id STRING, "
        "owner STRING, key_auth_enabled BOOLEAN, usage_tracking BOOLEAN, "
        "details_json STRING, is_current BOOLEAN, first_seen_at TIMESTAMP, "
        "last_seen_at TIMESTAMP, ingested_at TIMESTAMP) "
        "COMMENT 'Unified AI model catalog (Databricks UC/serving + Azure AI) "
        "with removal history'"
    )


def create_access_table_sql(catalog: str, schema: str) -> str:
    """DDL for the ai_model_access table. Pure."""
    return (
        f"CREATE TABLE IF NOT EXISTS {catalog}.{schema}.ai_model_access ("
        "workspace_id STRING, environment STRING, source STRING, model_key STRING, "
        "principal_id STRING, principal_name STRING, principal_type STRING, "
        "access_level STRING, role_or_privilege STRING, scope STRING, via STRING, "
        "details_json STRING, is_current BOOLEAN, first_seen_at TIMESTAMP, "
        "last_seen_at TIMESTAMP, ingested_at TIMESTAMP) "
        "COMMENT 'Identities that can access each cataloged AI model, "
        "with grant path'"
    )


_CATALOG_ATTRS = (
    "provider", "model_name", "model_version", "entity_type", "endpoint_name",
    "status", "region", "subscription_id", "resource_group", "resource_id",
    "owner", "key_auth_enabled", "usage_tracking", "details_json",
)

_ACCESS_ATTRS = ("principal_name", "principal_type", "access_level", "details_json")


def merge_catalog_sql(catalog: str, schema: str) -> str:
    """Upsert one source's snapshot; soft-resolve disappeared models.

    ``WHEN NOT MATCHED BY SOURCE`` flips is_current instead of deleting —
    a model leaving the catalog is itself signal. The predicate limits the
    resolve to the exact workspace/environment/source being refreshed so an
    unavailable source never resolves its rows.
    """
    fq = f"{catalog}.{schema}.ai_model_catalog"
    updates = ", ".join(f"t.{c} = s.{c}" for c in _CATALOG_ATTRS)
    columns = ("workspace_id", "environment", "source", "model_key", *_CATALOG_ATTRS)
    return (
        f"MERGE INTO {fq} t USING ("
        "SELECT :workspace_id AS workspace_id, :environment AS environment, item.* "
        f"FROM (SELECT explode(from_json(:rows, '{CATALOG_ROW_SCHEMA}')) AS item)"
        ") s "
        "ON t.workspace_id = s.workspace_id AND t.environment = s.environment "
        "AND t.source = s.source AND t.model_key = s.model_key "
        f"WHEN MATCHED THEN UPDATE SET {updates}, t.is_current = true, "
        "t.last_seen_at = current_timestamp(), t.ingested_at = current_timestamp() "
        f"WHEN NOT MATCHED THEN INSERT ({', '.join(columns)}, is_current, "
        "first_seen_at, last_seen_at, ingested_at) "
        f"VALUES ({', '.join(f's.{c}' for c in columns)}, true, "
        "current_timestamp(), current_timestamp(), current_timestamp()) "
        "WHEN NOT MATCHED BY SOURCE AND t.workspace_id = :workspace_id "
        "AND t.environment = :environment AND t.source = :source "
        "AND t.is_current THEN UPDATE SET t.is_current = false, "
        "t.ingested_at = current_timestamp()"
    )


def merge_access_sql(catalog: str, schema: str) -> str:
    """Upsert one source's access snapshot; soft-resolve revoked grants."""
    fq = f"{catalog}.{schema}.ai_model_access"
    updates = ", ".join(f"t.{c} = s.{c}" for c in _ACCESS_ATTRS)
    columns = (
        "workspace_id", "environment", "source", "model_key", "principal_id",
        "role_or_privilege", "scope", "via", *_ACCESS_ATTRS,
    )
    return (
        f"MERGE INTO {fq} t USING ("
        "SELECT :workspace_id AS workspace_id, :environment AS environment, item.* "
        f"FROM (SELECT explode(from_json(:rows, '{ACCESS_ROW_SCHEMA}')) AS item)"
        ") s "
        "ON t.workspace_id = s.workspace_id AND t.environment = s.environment "
        "AND t.source = s.source AND t.model_key = s.model_key "
        "AND t.principal_id = s.principal_id "
        "AND t.role_or_privilege = s.role_or_privilege AND t.scope = s.scope "
        f"WHEN MATCHED THEN UPDATE SET {updates}, t.via = s.via, t.is_current = true, "
        "t.last_seen_at = current_timestamp(), t.ingested_at = current_timestamp() "
        f"WHEN NOT MATCHED THEN INSERT ({', '.join(columns)}, is_current, "
        "first_seen_at, last_seen_at, ingested_at) "
        f"VALUES ({', '.join(f's.{c}' for c in columns)}, true, "
        "current_timestamp(), current_timestamp(), current_timestamp()) "
        "WHEN NOT MATCHED BY SOURCE AND t.workspace_id = :workspace_id "
        "AND t.environment = :environment AND t.source = :source "
        "AND t.is_current THEN UPDATE SET t.is_current = false, "
        "t.ingested_at = current_timestamp()"
    )


def _snapshot_params(
    rows: list[dict],
    workspace_id: str,
    environment: str,
    sources: list[str],
) -> dict[str, list[dict]]:
    """Validate a snapshot and split its rows per refreshed source. Pure."""
    if not workspace_id.strip() or not environment.strip():
        raise ValueError(
            "workspace_id and environment are required for catalog reconciliation"
        )
    if not sources:
        raise ValueError("at least one refreshed source is required")
    unknown = sorted(set(sources) - set(SOURCES))
    if unknown:
        raise ValueError(f"unknown catalog sources: {unknown}")
    outside = sorted(
        {str(row.get("source")) for row in rows} - set(sources)
    )
    if outside:
        raise ValueError(
            f"rows belong to sources that were not declared as refreshed: {outside}"
        )
    return {source: [r for r in rows if r.get("source") == source] for source in sources}


def store_catalog(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    rows: list[dict],
    *,
    workspace_id: str,
    environment: str,
    sources: list[str],
) -> int:
    """Reconcile the catalog snapshot, one MERGE per refreshed source."""
    by_source = _snapshot_params(rows, workspace_id, environment, sources)
    for source, scoped in by_source.items():
        params = {
            "rows": json.dumps(scoped, default=str),
            "workspace_id": workspace_id,
            "environment": environment,
            "source": source,
        }
        try:
            run_query(w, merge_catalog_sql(catalog, schema), warehouse_id, params)
        except Exception as exc:
            raise RuntimeError(
                f"Unable to reconcile required table "
                f"{catalog}.{schema}.ai_model_catalog; run the deployment "
                "schema_migrations job and verify writer grants."
            ) from exc
    return len(rows)


def store_access(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    rows: list[dict],
    *,
    workspace_id: str,
    environment: str,
    sources: list[str],
) -> int:
    """Reconcile the access snapshot, one MERGE per refreshed source."""
    by_source = _snapshot_params(rows, workspace_id, environment, sources)
    for source, scoped in by_source.items():
        params = {
            "rows": json.dumps(scoped, default=str),
            "workspace_id": workspace_id,
            "environment": environment,
            "source": source,
        }
        try:
            run_query(w, merge_access_sql(catalog, schema), warehouse_id, params)
        except Exception as exc:
            raise RuntimeError(
                f"Unable to reconcile required table "
                f"{catalog}.{schema}.ai_model_access; run the deployment "
                "schema_migrations job and verify writer grants."
            ) from exc
    return len(rows)


# --- reads (report/access commands and the digest collector) ---------------------

def read_catalog(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    workspace_id: str,
    environment: str,
    source: str | None = None,
) -> list[dict]:
    fq = f"{catalog}.{schema}.ai_model_catalog"
    sql = (
        f"SELECT * FROM {fq} WHERE workspace_id = :workspace_id "
        "AND environment = :environment AND is_current"
    )
    params: dict[str, int | str] = {
        "workspace_id": workspace_id,
        "environment": environment,
    }
    if source:
        sql += " AND source = :source"
        params["source"] = source
    sql += " ORDER BY source, model_key"
    return run_query(w, sql, warehouse_id, params)


def read_access(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    workspace_id: str,
    environment: str,
    model_key: str | None = None,
    principal: str | None = None,
) -> list[dict]:
    fq = f"{catalog}.{schema}.ai_model_access"
    sql = (
        f"SELECT * FROM {fq} WHERE workspace_id = :workspace_id "
        "AND environment = :environment AND is_current"
    )
    params: dict[str, int | str] = {
        "workspace_id": workspace_id,
        "environment": environment,
    }
    if model_key:
        sql += " AND model_key = :model_key"
        params["model_key"] = model_key
    if principal:
        sql += " AND (principal_name = :principal OR principal_id = :principal)"
        params["principal"] = principal
    sql += " ORDER BY source, model_key, principal_name"
    return run_query(w, sql, warehouse_id, params)

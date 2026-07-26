"""Application-centric cost attribution shared by jobs, API, and assistant.

The module deliberately keeps money in independent ledgers.  Azure Actual
cost and Databricks List cost are never converted or combined.  Application
identity is evidence-based: direct billing metadata, an observed app-resource
binding, or one of the configured billing tags.  Conflicting evidence fails
closed and remains visible outside the application's exact total.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from databricks.sdk import WorkspaceClient

from dbx_platform.system_tables import load_query, run_query

DEFAULT_APPLICATION_TAG_KEYS = ("application", "app", "project")
EXACT_ATTRIBUTION_METHODS = frozenset(
    {"DIRECT_METADATA", "DIRECT_TAG", "DIRECT_RESOURCE"}
)
ATTRIBUTION_METHODS = (
    "DIRECT_METADATA",
    "DIRECT_TAG",
    "DIRECT_RESOURCE",
    "CONFLICT",
    "SHARED_UNALLOCATED",
    "UNATTRIBUTED",
)
_WHITESPACE_RE = re.compile(r"\s+")
_TAG_EVIDENCE_SCHEMA = (
    "array<struct<evidence_id:string,snapshot_id:string,workspace_id:string,environment:string,"
    "subscription_id:string,scope_filter:string,query_start:date,query_end:date,"
    "usage_date:date,resource_id:string,resource_group:string,tag_key:string,"
    "tag_value:string,observed_cost:double,currency:string,observed_at:timestamp>>"
)
_AZURE_RESOURCE_SCHEMA = (
    "array<struct<evidence_id:string,snapshot_id:string,workspace_id:string,"
    "environment:string,"
    "subscription_id:string,scope_filter:string,query_start:date,query_end:date,"
    "usage_date:date,resource_id:string,resource_group:string,resource_name:string,"
    "resource_type:string,service:string,cost:double,currency:string,"
    "observed_at:timestamp>>"
)
_AZURE_SNAPSHOT_SCHEMA = (
    "array<struct<evidence_id:string,snapshot_id:string,workspace_id:string,"
    "environment:string,subscription_id:string,scope_filter:string,"
    "query_start:date,query_end:date,baseline_status:string,tag_status:string,"
    "tag_keys:string,row_count:bigint,job_id:string,run_id:string,"
    "trigger_type:string,observed_at:timestamp>>"
)
_BINDING_SCHEMA = (
    "array<struct<evidence_id:string,snapshot_id:string,workspace_id:string,"
    "environment:string,application_key:string,raw_application:string,"
    "app_id:string,resource_type:string,resource_id:string,resource_name:string,"
    "permission:string,effective_from:timestamp,observed_at:timestamp,"
    "details_json:string>>"
)
_BINDING_SNAPSHOT_SCHEMA = (
    "array<struct<evidence_id:string,snapshot_id:string,workspace_id:string,"
    "environment:string,app_count:bigint,binding_count:bigint,"
    "job_id:string,run_id:string,trigger_type:string,observed_at:timestamp>>"
)
_HEALTH_SCHEMA = (
    "array<struct<evidence_id:string,workspace_id:string,environment:string,"
    "subscription_id:string,scope_filter:string,source:string,status:string,"
    "last_success_at:timestamp,coverage_start:date,"
    "coverage_end:date,notes:string,job_id:string,run_id:string,"
    "trigger_type:string,observed_at:timestamp>>"
)
_APPLICATION_EVIDENCE_ROW_LIMIT = 100_000
_BINDING_EVIDENCE_ROW_LIMIT = 20_000
_SOURCE_FRESHNESS_GRACE_DAYS = 2
_SAFE_PRINCIPAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}$")
APPLICATION_EVIDENCE_TABLES = (
    "azure_cost_evidence_snapshots",
    "azure_cost_resource_evidence",
    "azure_cost_tag_evidence",
    "application_binding_snapshots",
    "application_resource_bindings",
    "application_source_health",
)


class EvidenceTruncatedError(RuntimeError):
    """A source exceeded the bounded interactive read and cannot be exact."""


def parse_application_tag_keys(
    value: str | Sequence[str] | None,
) -> tuple[str, ...]:
    """Return unique, case-insensitive tag keys in configured precedence."""

    if value is None:
        raw: Iterable[str] = DEFAULT_APPLICATION_TAG_KEYS
    elif isinstance(value, str):
        raw = value.split(",")
    else:
        raw = value
    keys = tuple(
        dict.fromkeys(
            str(item).strip().casefold()
            for item in raw
            if str(item).strip()
        )
    )
    if not keys:
        raise ValueError("At least one application identity tag key is required.")
    return keys


def normalize_application_key(value: Any) -> str | None:
    """Canonicalize an identity without discarding its original display value."""

    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    normalized = _WHITESPACE_RE.sub("-", normalized)
    return normalized or None


def _normalized_tags(tags: Mapping[str, Any] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in (tags or {}).items():
        raw_key = str(key).strip().casefold()
        raw_value = str(value).strip() if value is not None else ""
        if raw_key and raw_value:
            result[raw_key] = raw_value
    return result


def resolve_application_identity(
    *,
    metadata_application: Any = None,
    bound_application: Any = None,
    tags: Mapping[str, Any] | None = None,
    tag_keys: str | Sequence[str] | None = None,
    shared_scope: bool = False,
) -> dict[str, Any]:
    """Resolve one row's application using exact evidence only.

    Every present evidence source participates in conflict detection.  Tag
    precedence selects the display/tag key only after all accepted keys agree;
    it never hides a disagreement.
    """

    configured_keys = parse_application_tag_keys(tag_keys)
    normalized_tags = _normalized_tags(tags)
    candidates: list[dict[str, str]] = []

    def add(method: str, raw: Any, tag_key: str | None = None) -> None:
        key = normalize_application_key(raw)
        if key:
            candidates.append(
                {
                    "method": method,
                    "key": key,
                    "raw": str(raw).strip(),
                    "tag_key": tag_key or "",
                }
            )

    add("DIRECT_METADATA", metadata_application)
    add("DIRECT_RESOURCE", bound_application)
    for tag_key in configured_keys:
        add("DIRECT_TAG", normalized_tags.get(tag_key), tag_key)

    identities = sorted({item["key"] for item in candidates})
    if len(identities) > 1:
        return {
            "application_key": None,
            "raw_application": None,
            "attribution_method": "CONFLICT",
            "tag_key": None,
            "conflict_values": identities,
        }
    if not identities:
        return {
            "application_key": None,
            "raw_application": None,
            "attribution_method": (
                "SHARED_UNALLOCATED" if shared_scope else "UNATTRIBUTED"
            ),
            "tag_key": None,
            "conflict_values": [],
        }

    # Strongest evidence controls the method. Within tags, configured order
    # controls which raw value is displayed.
    rank = {"DIRECT_METADATA": 0, "DIRECT_RESOURCE": 1, "DIRECT_TAG": 2}
    tag_rank = {key: index for index, key in enumerate(configured_keys)}
    chosen = min(
        candidates,
        key=lambda item: (
            rank[item["method"]],
            tag_rank.get(item["tag_key"], len(tag_rank)),
        ),
    )
    return {
        "application_key": identities[0],
        "raw_application": chosen["raw"],
        "attribution_method": chosen["method"],
        "tag_key": chosen["tag_key"] or None,
        "conflict_values": [],
    }


def stable_evidence_id(row: Mapping[str, Any]) -> str:
    """Deterministic identity for an observed cost/binding evidence row."""

    fields = (
        "workspace_id",
        "environment",
        "usage_date",
        "source",
        "resource_type",
        "resource_id",
        "service",
        "workload",
        "currency",
        "pricing_basis",
        "cost",
        "application_key",
        "attribution_method",
        "tag_key",
        "raw_application",
        "scope",
    )
    canonical = json.dumps(
        {key: row.get(key) for key in fields},
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_application_table_statements(
    catalog: str,
    schema: str,
) -> list[tuple[str, str]]:
    """Deployment-only DDL for append-only attribution evidence."""

    fq = f"{catalog}.{schema}"
    append_only = "TBLPROPERTIES ('delta.appendOnly' = 'true')"
    return [
        (
            f"table {fq}.azure_cost_evidence_snapshots",
            f"CREATE TABLE IF NOT EXISTS {fq}.azure_cost_evidence_snapshots ("
            "evidence_id STRING NOT NULL, snapshot_id STRING NOT NULL, "
            "workspace_id STRING NOT NULL, environment STRING NOT NULL, "
            "subscription_id STRING NOT NULL, scope_filter STRING NOT NULL, "
            "query_start DATE NOT NULL, query_end DATE NOT NULL, "
            "baseline_status STRING NOT NULL, tag_status STRING NOT NULL, "
            "tag_keys STRING NOT NULL, row_count BIGINT NOT NULL, "
            "job_id STRING, run_id STRING, trigger_type STRING, "
            "observed_at TIMESTAMP NOT NULL) USING DELTA "
            f"{append_only}",
        ),
        (
            f"table {fq}.azure_cost_resource_evidence",
            f"CREATE TABLE IF NOT EXISTS {fq}.azure_cost_resource_evidence ("
            "evidence_id STRING NOT NULL, snapshot_id STRING NOT NULL, "
            "workspace_id STRING NOT NULL, "
            "environment STRING NOT NULL, subscription_id STRING NOT NULL, "
            "scope_filter STRING NOT NULL, query_start DATE NOT NULL, "
            "query_end DATE NOT NULL, usage_date DATE NOT NULL, "
            "resource_id STRING, resource_group STRING, resource_name STRING, "
            "resource_type STRING, service STRING, cost DOUBLE, currency STRING, "
            "observed_at TIMESTAMP NOT NULL) USING DELTA "
            f"{append_only}",
        ),
        (
            f"table {fq}.azure_cost_tag_evidence",
            f"CREATE TABLE IF NOT EXISTS {fq}.azure_cost_tag_evidence ("
            "evidence_id STRING NOT NULL, snapshot_id STRING NOT NULL, "
            "workspace_id STRING NOT NULL, "
            "environment STRING NOT NULL, subscription_id STRING NOT NULL, "
            "scope_filter STRING NOT NULL, query_start DATE NOT NULL, "
            "query_end DATE NOT NULL, usage_date DATE NOT NULL, "
            "resource_id STRING NOT NULL, resource_group STRING, "
            "tag_key STRING NOT NULL, tag_value STRING, observed_cost DOUBLE, "
            "currency STRING, observed_at TIMESTAMP NOT NULL) USING DELTA "
            f"{append_only}",
        ),
        (
            f"table {fq}.application_binding_snapshots",
            f"CREATE TABLE IF NOT EXISTS {fq}.application_binding_snapshots ("
            "evidence_id STRING NOT NULL, snapshot_id STRING NOT NULL, "
            "workspace_id STRING NOT NULL, environment STRING NOT NULL, "
            "app_count BIGINT NOT NULL, binding_count BIGINT NOT NULL, "
            "job_id STRING, run_id STRING, trigger_type STRING, "
            "observed_at TIMESTAMP NOT NULL) USING DELTA "
            f"{append_only}",
        ),
        (
            f"table {fq}.application_resource_bindings",
            f"CREATE TABLE IF NOT EXISTS {fq}.application_resource_bindings ("
            "evidence_id STRING NOT NULL, snapshot_id STRING NOT NULL, "
            "workspace_id STRING NOT NULL, environment STRING NOT NULL, "
            "application_key STRING NOT NULL, raw_application STRING NOT NULL, "
            "app_id STRING, resource_type STRING NOT NULL, resource_id STRING NOT NULL, "
            "resource_name STRING, permission STRING, effective_from TIMESTAMP NOT NULL, "
            "observed_at TIMESTAMP NOT NULL, details_json STRING) USING DELTA "
            f"{append_only}",
        ),
        (
            f"table {fq}.application_source_health",
            f"CREATE TABLE IF NOT EXISTS {fq}.application_source_health ("
            "evidence_id STRING NOT NULL, workspace_id STRING NOT NULL, "
            "environment STRING NOT NULL, subscription_id STRING, scope_filter STRING, "
            "source STRING NOT NULL, status STRING NOT NULL, "
            "last_success_at TIMESTAMP, coverage_start DATE, coverage_end DATE, "
            "notes STRING, job_id STRING, run_id STRING, trigger_type STRING, "
            "observed_at TIMESTAMP NOT NULL) USING DELTA "
            f"{append_only}",
        ),
    ]


def application_table_grant_statements(
    catalog: str,
    schema: str,
    *,
    app_service_principal: str,
    runtime_executor_service_principal: str = "",
) -> list[tuple[str, str]]:
    """Least-privilege table grants applied by deployment migrations."""

    principals = {
        "app": (str(app_service_principal), "SELECT"),
        "runtime": (
            str(runtime_executor_service_principal),
            "SELECT, MODIFY",
        ),
    }
    for label, (principal, _permissions) in principals.items():
        if principal and not _SAFE_PRINCIPAL_RE.fullmatch(principal):
            raise ValueError(f"Unsafe {label} service principal: {principal!r}")
    statements = []
    for table in APPLICATION_EVIDENCE_TABLES:
        for label, (principal, permissions) in principals.items():
            if not principal:
                continue
            statements.append(
                (
                    f"grant {label} application evidence {table}",
                    f"GRANT {permissions} ON TABLE {catalog}.{schema}.{table} "
                    f"TO `{principal}`",
                )
            )
    return statements


def prepare_azure_resource_evidence(
    rows: Sequence[Mapping[str, Any]],
    *,
    workspace_id: str,
    environment: str,
    subscription_id: str,
    query_start: str,
    query_end: str,
    snapshot_id: str,
    observed_at: str | None = None,
) -> list[dict[str, Any]]:
    """Attach durable subscription scope to the single Azure cost baseline."""

    observed_at = observed_at or datetime.now(UTC).isoformat()
    prepared = []
    for source in rows:
        row = {
            "snapshot_id": str(snapshot_id),
            "workspace_id": str(workspace_id),
            "environment": str(environment),
            "subscription_id": str(subscription_id),
            "scope_filter": "subscription",
            "query_start": str(query_start)[:10],
            "query_end": str(query_end)[:10],
            "usage_date": str(source.get("usage_date") or "")[:10],
            "resource_id": str(source.get("resource_id") or ""),
            "resource_group": str(source.get("resource_group") or ""),
            "resource_name": str(
                source.get("resource_name") or "Unidentified Azure charge"
            ),
            "resource_type": str(source.get("resource_type") or "unidentified"),
            "service": str(source.get("service") or "Azure"),
            "cost": float(source.get("cost") or 0),
            "currency": str(source.get("currency") or "UNRESOLVED").upper(),
            "observed_at": observed_at,
        }
        if not row["usage_date"]:
            continue
        row["evidence_id"] = _hash_document(
            {
                key: row[key]
                for key in (
                    "workspace_id",
                    "snapshot_id",
                    "environment",
                    "subscription_id",
                    "scope_filter",
                    "query_start",
                    "query_end",
                    "usage_date",
                    "resource_id",
                    "resource_group",
                    "resource_type",
                    "cost",
                    "currency",
                )
            }
        )
        prepared.append(row)
    return prepared


def azure_evidence_snapshot_id(
    *,
    workspace_id: str,
    environment: str,
    subscription_id: str,
    query_start: str,
    query_end: str,
    observation_id: str,
) -> str:
    """Stable for a retry of one attested observation, distinct across runs."""

    return _hash_document(
        {
            "workspace_id": workspace_id,
            "environment": environment,
            "subscription_id": subscription_id,
            "query_start": str(query_start)[:10],
            "query_end": str(query_end)[:10],
            "observation_id": observation_id,
        }
    )


def prepare_azure_evidence_snapshot(
    *,
    snapshot_id: str,
    workspace_id: str,
    environment: str,
    subscription_id: str,
    query_start: str,
    query_end: str,
    baseline_status: str,
    tag_status: str,
    tag_keys: str | Sequence[str],
    row_count: int,
    observed_at: str,
    job_id: str = "",
    run_id: str = "",
    trigger_type: str = "",
) -> dict[str, Any]:
    row = {
        "snapshot_id": str(snapshot_id),
        "workspace_id": str(workspace_id),
        "environment": str(environment),
        "subscription_id": str(subscription_id),
        "scope_filter": "subscription",
        "query_start": str(query_start)[:10],
        "query_end": str(query_end)[:10],
        "baseline_status": str(baseline_status),
        "tag_status": str(tag_status),
        "tag_keys": ",".join(parse_application_tag_keys(tag_keys)),
        "row_count": int(row_count),
        "job_id": str(job_id),
        "run_id": str(run_id),
        "trigger_type": str(trigger_type),
        "observed_at": observed_at,
    }
    row["evidence_id"] = _hash_document(row)
    return row


def _hash_document(document: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        dict(document),
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def prepare_tag_evidence(
    rows: Sequence[Mapping[str, Any]],
    *,
    workspace_id: str,
    environment: str,
    subscription_id: str,
    query_start: str,
    query_end: str,
    snapshot_id: str = "",
    observed_at: str | None = None,
) -> list[dict[str, Any]]:
    """Attach durable scope/provenance to Cost Management TagKey rows."""

    observed_at = observed_at or datetime.now(UTC).isoformat()
    prepared = []
    for source in rows:
        row = {
            "snapshot_id": str(snapshot_id) or _hash_document(
                {
                    "workspace_id": workspace_id,
                    "environment": environment,
                    "subscription_id": subscription_id,
                    "query_start": str(query_start)[:10],
                    "query_end": str(query_end)[:10],
                    "observed_at": observed_at,
                }
            ),
            "workspace_id": str(workspace_id),
            "environment": str(environment),
            "subscription_id": str(subscription_id),
            # Tag discovery is subscription-scoped and intentionally
            # independent of the coarse workspace RG allowlist.
            "scope_filter": "subscription",
            "query_start": str(query_start)[:10],
            "query_end": str(query_end)[:10],
            "usage_date": str(source.get("usage_date") or "")[:10],
            "resource_id": str(source.get("resource_id") or ""),
            "resource_group": str(source.get("resource_group") or ""),
            "tag_key": str(source.get("tag_key") or "").strip().casefold(),
            "tag_value": str(source.get("tag_value") or "").strip(),
            "observed_cost": float(source.get("observed_cost") or 0),
            "currency": str(source.get("currency") or "UNRESOLVED").upper(),
            "observed_at": observed_at,
        }
        if not row["resource_id"] or not row["tag_key"] or not row["usage_date"]:
            continue
        row["evidence_id"] = _hash_document(
            {
                key: row[key]
                for key in (
                    "workspace_id",
                    "snapshot_id",
                    "environment",
                    "subscription_id",
                    "scope_filter",
                    "query_start",
                    "query_end",
                    "usage_date",
                    "resource_id",
                    "tag_key",
                    "tag_value",
                    "observed_cost",
                    "currency",
                )
            }
        )
        prepared.append(row)
    return prepared


def _enum_text(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _object_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "as_dict"):
        return dict(value.as_dict())
    return {}


def _resource_target(resource_type: str, value: Mapping[str, Any]) -> str:
    candidates = {
        "app": ("name",),
        "database": ("instance_name", "database_name"),
        "experiment": ("experiment_id",),
        "genie_space": ("space_id",),
        "job": ("id",),
        "postgres": ("database", "branch"),
        "secret": ("scope", "key"),
        "serving_endpoint": ("name",),
        "sql_warehouse": ("id",),
        "uc_securable": ("securable_full_name",),
    }.get(resource_type, ())
    values = [str(value.get(key) or "").strip() for key in candidates]
    values = [item for item in values if item]
    return "/".join(values)


def _postgres_aliases(w: WorkspaceClient, detail: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Resolve governed Postgres paths to stable IDs used by billing metadata."""

    database_path = str(detail.get("database") or "")
    branch_path = str(detail.get("branch") or "")
    source_path = database_path or branch_path
    project_match = re.match(r"^(projects/[^/]+)", source_path)
    branch_match = re.match(r"^(projects/[^/]+/branches/[^/]+)", source_path)
    aliases: list[tuple[str, str]] = []
    # A branch/database binding must not fall back to a project-wide alias:
    # unrelated branches in one project otherwise inherit the same app.
    if project_match and not branch_match:
        project = w.postgres.get_project(project_match.group(1))
        aliases.extend(
            (kind, str(value))
            for kind, value in (
                ("project_id", getattr(project, "project_id", None)),
                ("project_uid", getattr(project, "uid", None)),
            )
            if value
        )
    if branch_match:
        branch = w.postgres.get_branch(branch_match.group(1))
        aliases.extend(
            (kind, str(value))
            for kind, value in (
                ("branch_id", getattr(branch, "branch_id", None)),
                ("branch_uid", getattr(branch, "uid", None)),
            )
            if value
        )
    if database_path:
        database = w.postgres.get_database(database_path)
        database_id = getattr(database, "database_id", None)
        if database_id:
            aliases.append(("database_id", str(database_id)))
    return list(dict.fromkeys(aliases))


def fetch_application_bindings(
    w: WorkspaceClient,
    *,
    workspace_id: str,
    environment: str,
    observed_at: str | None = None,
    diagnostics: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Snapshot every Databricks App and its generic resource bindings."""

    observed_at = observed_at or datetime.now(UTC).isoformat()
    snapshot_id = _hash_document(
        {
            "workspace_id": workspace_id,
            "environment": environment,
            "observed_at": observed_at,
        }
    )
    rows = []
    for listed in w.apps.list():
        app_name = str(getattr(listed, "name", "") or "").strip()
        if not app_name:
            continue
        app = w.apps.get(app_name)
        application_key = normalize_application_key(app_name)
        app_id = str(getattr(app, "id", "") or "")
        # A self-binding makes a snapshot durable even for an App that has no
        # bound downstream resources.
        resources = [
            {
                "name": app_name,
                "app": {"name": app_name, "permission": "CAN_USE"},
            },
            *[
                _object_dict(resource)
                for resource in (getattr(app, "resources", None) or [])
            ],
        ]
        for resource in resources:
            binding_name = str(resource.get("name") or "")
            for resource_type in (
                "app",
                "database",
                "experiment",
                "genie_space",
                "job",
                "postgres",
                "secret",
                "serving_endpoint",
                "sql_warehouse",
                "uc_securable",
            ):
                detail = _object_dict(resource.get(resource_type))
                if not detail:
                    continue
                targets: list[tuple[str, str]]
                if resource_type == "app":
                    targets = [("app_id", app_id)] if app_id else []
                    if not targets and diagnostics is not None:
                        diagnostics.append(f"{app_name}/app: stable app ID unavailable")
                elif resource_type == "serving_endpoint":
                    endpoint_name = str(detail.get("name") or "")
                    try:
                        endpoint = w.serving_endpoints.get(endpoint_name)
                        endpoint_id = str(getattr(endpoint, "id", "") or "")
                    except Exception as exc:  # noqa: BLE001 - partial health
                        endpoint_id = ""
                        if diagnostics is not None:
                            diagnostics.append(
                                f"{app_name}/{binding_name or endpoint_name}: "
                                f"{type(exc).__name__}"
                            )
                    targets = [("endpoint_id", endpoint_id)] if endpoint_id else []
                elif resource_type == "database":
                    targets = []
                    if diagnostics is not None:
                        diagnostics.append(
                            f"{app_name}/{binding_name or 'database'}: "
                            "stable billing ID unavailable"
                        )
                elif resource_type == "postgres":
                    try:
                        targets = _postgres_aliases(w, detail)
                    except Exception as exc:  # noqa: BLE001 - caller reports partial health
                        targets = []
                        if diagnostics is not None:
                            diagnostics.append(
                                f"{app_name}/{binding_name or 'postgres'}: "
                                f"{type(exc).__name__}"
                            )
                    if not targets:
                        if diagnostics is not None and not any(
                            item.startswith(f"{app_name}/{binding_name or 'postgres'}:")
                            for item in diagnostics
                        ):
                            diagnostics.append(
                                f"{app_name}/{binding_name or 'postgres'}: "
                                "stable billing IDs unavailable"
                            )
                        break
                else:
                    resource_id = _resource_target(resource_type, detail)
                    targets = [(resource_type, resource_id)] if resource_id else []
                if not targets:
                    continue
                normalized_type = {
                    "serving_endpoint": "endpoint",
                    "sql_warehouse": "warehouse",
                    "postgres": "database",
                }.get(resource_type, resource_type)
                permission = _enum_text(detail.get("permission"))
                for alias_kind, resource_id in targets:
                    row = {
                        "snapshot_id": snapshot_id,
                        "workspace_id": str(workspace_id),
                        "environment": str(environment),
                        "application_key": application_key,
                        "raw_application": app_name,
                        "app_id": app_id,
                        "resource_type": normalized_type,
                        "resource_id": resource_id,
                        "resource_name": binding_name or resource_id,
                        "permission": permission,
                        "effective_from": observed_at,
                        "observed_at": observed_at,
                        "details_json": json.dumps(
                            {**detail, "billing_alias_kind": alias_kind},
                            default=str,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    }
                    row["evidence_id"] = _hash_document(
                        {
                            key: row[key]
                            for key in (
                                "snapshot_id",
                                "workspace_id",
                                "environment",
                                "application_key",
                                "app_id",
                                "resource_type",
                                "resource_id",
                                "permission",
                            )
                        }
                    )
                    rows.append(row)
                break
    return rows


def prepare_binding_snapshot(
    bindings: Sequence[Mapping[str, Any]],
    *,
    workspace_id: str,
    environment: str,
    observed_at: str,
    job_id: str = "",
    run_id: str = "",
    trigger_type: str = "",
) -> dict[str, Any]:
    """Record a complete snapshot, including the valid empty-snapshot case."""

    snapshot_ids = {
        str(row.get("snapshot_id") or "") for row in bindings if row.get("snapshot_id")
    }
    if len(snapshot_ids) > 1:
        raise ValueError("Binding rows belong to multiple snapshots.")
    snapshot_id = next(iter(snapshot_ids), None) or _hash_document(
        {
            "workspace_id": workspace_id,
            "environment": environment,
            "observed_at": observed_at,
        }
    )
    row = {
        "snapshot_id": snapshot_id,
        "workspace_id": str(workspace_id),
        "environment": str(environment),
        "app_count": len(
            {
                str(binding.get("application_key"))
                for binding in bindings
                if binding.get("application_key")
            }
        ),
        "binding_count": len(bindings),
        "job_id": str(job_id),
        "run_id": str(run_id),
        "trigger_type": str(trigger_type),
        "observed_at": observed_at,
    }
    row["evidence_id"] = _hash_document(row)
    return row


def prepare_source_health(
    *,
    workspace_id: str,
    environment: str,
    source: str,
    status: str,
    notes: str,
    subscription_id: str = "",
    scope_filter: str = "",
    coverage_start: str | None = None,
    coverage_end: str | None = None,
    last_success_at: str | None = None,
    job_id: str = "",
    run_id: str = "",
    trigger_type: str = "",
    observed_at: str | None = None,
) -> dict[str, Any]:
    observed_at = observed_at or datetime.now(UTC).isoformat()
    row = {
        "workspace_id": str(workspace_id),
        "environment": str(environment),
        "subscription_id": str(subscription_id),
        "scope_filter": str(scope_filter),
        "source": str(source),
        "status": str(status),
        "last_success_at": last_success_at,
        "coverage_start": str(coverage_start)[:10] if coverage_start else None,
        "coverage_end": str(coverage_end)[:10] if coverage_end else None,
        "notes": str(notes),
        "job_id": str(job_id),
        "run_id": str(run_id),
        "trigger_type": str(trigger_type),
        "observed_at": observed_at,
    }
    row["evidence_id"] = _hash_document(row)
    return row


def _append_only_merge_sql(
    table: str,
    schema: str,
    columns: Sequence[str],
) -> str:
    selected = ", ".join(f"item.{column}" for column in columns)
    insert_columns = ", ".join(columns)
    return (
        f"MERGE INTO {table} t USING (SELECT {selected} "
        f"FROM (SELECT explode(from_json(:rows, '{schema}')) AS item)) s "
        "ON t.evidence_id = s.evidence_id "
        f"WHEN NOT MATCHED THEN INSERT ({insert_columns}) VALUES ({selected})"
    )


def append_application_evidence(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    *,
    azure_snapshot_rows: Sequence[Mapping[str, Any]] = (),
    azure_resource_rows: Sequence[Mapping[str, Any]] = (),
    tag_rows: Sequence[Mapping[str, Any]] = (),
    binding_rows: Sequence[Mapping[str, Any]] = (),
    binding_snapshot_rows: Sequence[Mapping[str, Any]] = (),
    health_rows: Sequence[Mapping[str, Any]] = (),
) -> dict[str, int]:
    """Idempotently append immutable evidence; no historical row is updated."""

    batches = (
        (
            "azure_cost_evidence_snapshots",
            _AZURE_SNAPSHOT_SCHEMA,
            (
                "evidence_id",
                "snapshot_id",
                "workspace_id",
                "environment",
                "subscription_id",
                "scope_filter",
                "query_start",
                "query_end",
                "baseline_status",
                "tag_status",
                "tag_keys",
                "row_count",
                "job_id",
                "run_id",
                "trigger_type",
                "observed_at",
            ),
            azure_snapshot_rows,
        ),
        (
            "azure_cost_resource_evidence",
            _AZURE_RESOURCE_SCHEMA,
            (
                "evidence_id",
                "snapshot_id",
                "workspace_id",
                "environment",
                "subscription_id",
                "scope_filter",
                "query_start",
                "query_end",
                "usage_date",
                "resource_id",
                "resource_group",
                "resource_name",
                "resource_type",
                "service",
                "cost",
                "currency",
                "observed_at",
            ),
            azure_resource_rows,
        ),
        (
            "azure_cost_tag_evidence",
            _TAG_EVIDENCE_SCHEMA,
            (
                "evidence_id",
                "snapshot_id",
                "workspace_id",
                "environment",
                "subscription_id",
                "scope_filter",
                "query_start",
                "query_end",
                "usage_date",
                "resource_id",
                "resource_group",
                "tag_key",
                "tag_value",
                "observed_cost",
                "currency",
                "observed_at",
            ),
            tag_rows,
        ),
        (
            "application_resource_bindings",
            _BINDING_SCHEMA,
            (
                "evidence_id",
                "snapshot_id",
                "workspace_id",
                "environment",
                "application_key",
                "raw_application",
                "app_id",
                "resource_type",
                "resource_id",
                "resource_name",
                "permission",
                "effective_from",
                "observed_at",
                "details_json",
            ),
            binding_rows,
        ),
        (
            "application_binding_snapshots",
            _BINDING_SNAPSHOT_SCHEMA,
            (
                "evidence_id",
                "snapshot_id",
                "workspace_id",
                "environment",
                "app_count",
                "binding_count",
                "job_id",
                "run_id",
                "trigger_type",
                "observed_at",
            ),
            binding_snapshot_rows,
        ),
        (
            "application_source_health",
            _HEALTH_SCHEMA,
            (
                "evidence_id",
                "workspace_id",
                "environment",
                "subscription_id",
                "scope_filter",
                "source",
                "status",
                "last_success_at",
                "coverage_start",
                "coverage_end",
                "notes",
                "job_id",
                "run_id",
                "trigger_type",
                "observed_at",
            ),
            health_rows,
        ),
    )
    # A complete snapshot manifest is the visibility boundary. Append all
    # immutable rows first so a mid-run failure can never expose a partial
    # Azure observation as current evidence.
    batches = tuple(
        batch for batch in batches if batch[0] != "azure_cost_evidence_snapshots"
    ) + tuple(
        batch for batch in batches if batch[0] == "azure_cost_evidence_snapshots"
    )
    counts = {}
    for table_name, row_schema, columns, batch_rows in batches:
        counts[table_name] = len(batch_rows)
        if not batch_rows:
            continue
        table = f"{catalog}.{schema}.{table_name}"
        sql = _append_only_merge_sql(table, row_schema, columns)
        try:
            for offset in range(0, len(batch_rows), 500):
                run_query(
                    w,
                    sql,
                    warehouse_id,
                    {
                        "rows": json.dumps(
                            list(batch_rows[offset : offset + 500]),
                            default=str,
                        )
                    },
                )
        except Exception as exc:
            raise RuntimeError(
                f"Unable to append required table {table}; run the deployment "
                "schema_migrations job and verify writer grants."
            ) from exc
    return counts


def _json_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _binding_index(
    bindings: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], list[Mapping[str, Any]]]:
    index: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for binding in bindings:
        resource_type = str(binding.get("resource_type") or "").strip().casefold()
        resource_id = str(binding.get("resource_id") or "").strip().casefold()
        if resource_type and resource_id:
            index[(resource_type, resource_id)].append(binding)
    return index


def _active_bindings(
    bindings: Sequence[Mapping[str, Any]],
    usage_date: Any,
) -> list[Mapping[str, Any]]:
    day = str(usage_date or "")[:10]
    active: list[Mapping[str, Any]] = []
    by_application: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for binding in bindings:
        application = normalize_application_key(
            binding.get("raw_application") or binding.get("application_key")
        )
        if application:
            by_application[application].append(binding)

    for application_bindings in by_application.values():
        ordered = sorted(
            application_bindings,
            key=lambda row: str(
                row.get("effective_from") or row.get("observed_at") or ""
            ),
        )
        observed_days = {
            str(row.get("effective_from") or row.get("observed_at") or "")[:10]
            for row in ordered
        }
        chosen: Mapping[str, Any] | None = None
        for binding in ordered:
            observed = str(
                binding.get("effective_from") or binding.get("observed_at") or ""
            )[:10]
            if not observed or not day:
                continue
            try:
                first_exact_day = (
                    date.fromisoformat(observed) + timedelta(days=1)
                ).isoformat()
            except ValueError:
                continue
            if day < first_exact_day:
                continue
            effective_to = str(binding.get("effective_to") or "")[:10]
            if effective_to and day >= effective_to:
                # A same-day follow-up observation of the same binding proves
                # continuity. If the resource is omitted, the prior interval
                # ends and that day remains conservatively unattributed.
                if day != effective_to or effective_to not in observed_days:
                    continue
            if not effective_to:
                try:
                    freshness_end = (
                        date.fromisoformat(observed)
                        + timedelta(days=_SOURCE_FRESHNESS_GRACE_DAYS)
                    ).isoformat()
                except ValueError:
                    continue
                if day >= freshness_end:
                    continue
            chosen = binding
        if chosen is not None:
            active.append(chosen)
    return active


def _active_bound_applications(
    bindings: Sequence[Mapping[str, Any]],
    usage_date: Any,
) -> list[str]:
    active = []
    for binding in _active_bindings(bindings, usage_date):
        effective = str(
            binding.get("effective_from") or binding.get("observed_at") or ""
        )[:10]
        if not effective:
            continue
        value = binding.get("raw_application") or binding.get("application_key")
        if value:
            active.append(str(value))
    return sorted(set(active), key=str.casefold)


def resolve_evidence_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    bindings: Sequence[Mapping[str, Any]] = (),
    tag_keys: str | Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Normalize provider rows into the canonical application evidence shape."""

    configured_keys = parse_application_tag_keys(tag_keys)
    binding_index = _binding_index(bindings)
    resolved: list[dict[str, Any]] = []
    for source_row in rows:
        row = dict(source_row)
        resource_type = str(row.get("resource_type") or "unattributed")
        resource_id = str(row.get("resource_id") or "")
        resource_aliases = _json_mapping(row.get("resource_aliases_json"))
        alias_items = {
            str(key).casefold(): str(value).strip()
            for key, value in resource_aliases.items()
            if value not in (None, "")
        }
        if resource_type.casefold() == "database":
            specific_aliases = {
                value
                for key, value in alias_items.items()
                if "branch" in key or "database" in key
            }
            # Project IDs are never exact when a billing row identifies a
            # branch/database. This prevents sibling branches in one project
            # from inheriting an app binding.
            alias_values = specific_aliases or set(alias_items.values())
        else:
            alias_values = set(alias_items.values())
        lookup_ids = {resource_id, *alias_values}
        candidate_bindings = [
            binding
            for lookup_id in lookup_ids
            for binding in binding_index.get(
                (resource_type.casefold(), lookup_id.casefold()), []
            )
            if lookup_id
        ]
        candidate_bindings = list(
            {
                str(binding.get("evidence_id") or id(binding)): binding
                for binding in candidate_bindings
            }.values()
        )
        active_binding_rows = _active_bindings(
            candidate_bindings,
            row.get("usage_date"),
        )
        active_bindings = [
            str(binding.get("raw_application") or binding.get("application_key"))
            for binding in active_binding_rows
            if binding.get("raw_application") or binding.get("application_key")
        ]
        bound_application: str | None = None
        binding_conflict: list[str] = []
        if len({normalize_application_key(value) for value in active_bindings}) == 1:
            bound_application = active_bindings[0] if active_bindings else None
        elif active_bindings:
            binding_conflict = [
                value
                for value in (
                    normalize_application_key(item) for item in active_bindings
                )
                if value
            ]

        tags = _json_mapping(row.get("tags") or row.get("tags_json"))
        for key in configured_keys:
            direct_value = row.get(f"{key}_tag")
            if direct_value not in (None, ""):
                tags[key] = direct_value
        identity = resolve_application_identity(
            metadata_application=row.get("metadata_application"),
            bound_application=bound_application,
            tags=tags,
            tag_keys=configured_keys,
            shared_scope=bool(row.get("shared_scope")),
        )
        forced_conflicts = sorted(
            {
                value
                for value in (
                    normalize_application_key(item)
                    for item in (row.get("forced_conflict_values") or [])
                )
                if value
            }
        )
        if len(forced_conflicts) > 1 or (
            row.get("force_conflict") and forced_conflicts
        ):
            identity = {
                "application_key": None,
                "raw_application": None,
                "attribution_method": "CONFLICT",
                "tag_key": None,
                "conflict_values": forced_conflicts,
            }
        elif row.get("force_unresolved"):
            identity = {
                "application_key": None,
                "raw_application": None,
                "attribution_method": (
                    "SHARED_UNALLOCATED"
                    if row.get("shared_scope")
                    else "UNATTRIBUTED"
                ),
                "tag_key": None,
                "conflict_values": [],
            }
        if binding_conflict:
            combined_conflicts = (
                set(identity.get("conflict_values", []))
                | set(binding_conflict)
                | (
                    {str(identity["application_key"])}
                    if identity.get("application_key")
                    else set()
                )
            )
            identity = {
                "application_key": None,
                "raw_application": None,
                "attribution_method": "CONFLICT",
                "tag_key": None,
                "conflict_values": sorted(combined_conflicts),
            }
        raw_cost = row.get("cost")
        cost_known = bool(
            row.get("cost_known", raw_cost is not None)
        ) and not bool(row.get("inventory_only"))
        source_evidence_id = str(
            row.get("evidence_id") or stable_evidence_id({**row, **identity})
        )
        evidence_refs = {
            source_evidence_id,
            *(
                str(value)
                for value in (row.get("evidence_refs") or [])
                if value
            ),
            *(
                str(binding.get("evidence_id"))
                for binding in active_binding_rows
                if binding.get("evidence_id")
            ),
        }
        binding_attestation = next(
            (
                binding
                for binding in reversed(active_binding_rows)
                if binding.get("job_id") or binding.get("run_id")
            ),
            {},
        )
        normalized = {
            "workspace_id": str(row.get("workspace_id") or ""),
            "environment": str(row.get("environment") or ""),
            "usage_date": str(row.get("usage_date") or "")[:10],
            "source": str(row.get("source") or ""),
            "resource_type": resource_type,
            "resource_id": resource_id,
            "resource_name": str(row.get("resource_name") or resource_id),
            "resource_group": str(row.get("resource_group") or ""),
            "resource_aliases": resource_aliases,
            "service": str(row.get("service") or ""),
            "workload": str(row.get("workload") or ""),
            **identity,
            "tags": tags,
            "identity_tags": {
                key: value for key, value in tags.items() if key in configured_keys
            },
            "tag_observations": [
                dict(item) for item in (row.get("tag_observations") or [])
            ],
            "cost": float(raw_cost or 0),
            "cost_known": cost_known,
            "inventory_only": bool(row.get("inventory_only")),
            "currency": str(row.get("currency") or "UNRESOLVED").upper(),
            "pricing_basis": str(row.get("pricing_basis") or "UNRESOLVED").upper(),
            "evidence_at": row.get("evidence_at") or row.get("ingested_at"),
            "scope": str(row.get("scope") or ""),
            "snapshot_id": str(
                row.get("snapshot_id")
                or binding_attestation.get("snapshot_id")
                or ""
            ),
            "evidence_refs": sorted(evidence_refs),
            "job_id": str(
                row.get("job_id") or binding_attestation.get("job_id") or ""
            ),
            "run_id": str(
                row.get("run_id") or binding_attestation.get("run_id") or ""
            ),
            "trigger_type": str(
                row.get("trigger_type")
                or binding_attestation.get("trigger_type")
                or ""
            ),
            "unpriced_usage_quantity": float(
                row.get("unpriced_usage_quantity") or 0
            ),
        }
        normalized["evidence_id"] = source_evidence_id
        resolved.append(normalized)
    return resolved


def _ledger_id(source: str, pricing_basis: str, currency: str) -> str:
    return "::".join((source, pricing_basis, currency)).casefold()


def _ledger_title(source: str, pricing_basis: str, currency: str) -> str:
    source_title = "Azure" if source.casefold() == "azure" else "Databricks"
    basis_title = {
        "AZURE_ACTUAL": "Actual pre-tax",
        "DATABRICKS_LIST": "List",
    }.get(pricing_basis.upper(), pricing_basis.replace("_", " ").title())
    return f"{source_title} {basis_title} · {currency}"


def _source_status(
    source: str,
    source_health: Sequence[Mapping[str, Any]],
) -> str:
    relevant = [
        str(row.get("status") or "").casefold()
        for row in source_health
        if str(row.get("source") or "").casefold().startswith(source.casefold())
    ]
    if not relevant:
        return "unavailable"
    if any(status in {"unavailable", "not_configured", "truncated"} for status in relevant):
        return "unavailable"
    if any(status in {"partial", "stale", "degraded"} for status in relevant):
        return "partial"
    return "healthy"


def _ledger_source_status(
    source: str,
    rows: Sequence[Mapping[str, Any]],
    source_health: Sequence[Mapping[str, Any]],
) -> str:
    source_key = source.casefold()
    required_sources: tuple[str, ...]
    methods = {str(row.get("attribution_method") or "") for row in rows}
    if source_key == "databricks":
        required = ["databricks_billing"]
        if "DIRECT_RESOURCE" in methods:
            required.append("databricks_app_bindings")
        required_sources = tuple(required)
    elif source_key == "azure":
        required = ["azure_cost_resources"]
        if "DIRECT_TAG" in methods:
            required.append("azure_cost_tags")
        required_sources = tuple(required)
    else:
        required_sources = (source_key,)
    relevant = [
        row
        for row in source_health
        if str(row.get("source") or "").casefold() in required_sources
    ]
    if len({str(row.get("source") or "").casefold() for row in relevant}) < len(
        set(required_sources)
    ):
        return "unavailable"
    statuses = {
        (
            "healthy"
            if str(row.get("source") or "").casefold()
            == "databricks_app_bindings"
            and str(row.get("status") or "").casefold() == "partial"
            else str(row.get("status") or "").casefold()
        )
        for row in relevant
    }
    if statuses.intersection({"unavailable", "not_configured", "truncated"}):
        return "unavailable"
    if statuses.intersection({"partial", "stale", "degraded"}):
        return "partial"
    return "healthy"


def _trend_pct(rows: Sequence[Mapping[str, Any]]) -> float | None:
    daily: dict[date, float] = defaultdict(float)
    for row in rows:
        if not row.get("cost_known", True):
            continue
        try:
            day = date.fromisoformat(str(row.get("usage_date") or "")[:10])
        except ValueError:
            continue
        daily[day] += float(row.get("cost") or 0)
    if len(daily) < 2:
        return None
    first, last = min(daily), max(daily)
    midpoint = first + timedelta(days=((last - first).days + 1) // 2)
    previous = sum(value for day, value in daily.items() if day < midpoint)
    current = sum(value for day, value in daily.items() if day >= midpoint)
    if previous == 0:
        return None
    return round((current - previous) / abs(previous) * 100, 1)


def _iso_min(values: Iterable[Any]) -> str | None:
    present = [str(value) for value in values if value not in (None, "")]
    return min(present) if present else None


def _iso_max(values: Iterable[Any]) -> str | None:
    present = [str(value) for value in values if value not in (None, "")]
    return max(present) if present else None


def _ledger_rows(
    exact_rows: Sequence[Mapping[str, Any]],
    all_rows: Sequence[Mapping[str, Any]],
    source_health: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    exact_groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    all_groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in exact_rows:
        if not row.get("cost_known", True):
            continue
        exact_groups[
            (
                str(row.get("source") or ""),
                str(row.get("pricing_basis") or "UNRESOLVED"),
                str(row.get("currency") or "UNRESOLVED"),
            )
        ].append(row)
    for row in all_rows:
        if not row.get("cost_known", True):
            continue
        all_groups[
            (
                str(row.get("source") or ""),
                str(row.get("pricing_basis") or "UNRESOLVED"),
                str(row.get("currency") or "UNRESOLVED"),
            )
        ].append(row)

    ledgers = []
    application_keys = {
        str(row.get("application_key"))
        for row in exact_rows
        if row.get("application_key")
    }
    for (source, basis, currency), rows in exact_groups.items():
        entire_ledger = all_groups[(source, basis, currency)]
        related_conflicts = [
            row
            for row in entire_ledger
            if row.get("attribution_method") == "CONFLICT"
            and application_keys.intersection(row.get("conflict_values") or [])
        ]
        amount = sum(float(row.get("cost") or 0) for row in rows)
        status = _ledger_source_status(source, rows, source_health)
        ledgers.append(
            {
                "id": _ledger_id(source, basis, currency),
                "source": source,
                "title": _ledger_title(source, basis, currency),
                "amount": round(amount, 4),
                "currency": currency,
                "pricing_basis": basis,
                "attributed_cost": round(amount, 4),
                "unallocated_cost": round(
                    sum(float(row.get("cost") or 0) for row in related_conflicts),
                    4,
                ),
                "coverage_start": _iso_min(row.get("usage_date") for row in rows),
                "coverage_end": _iso_max(row.get("usage_date") for row in rows),
                "freshness": _iso_max(row.get("evidence_at") for row in rows),
                "trend_pct": _trend_pct(rows),
                "scope": sorted(
                    {str(row.get("scope")) for row in rows if row.get("scope")}
                ),
                "status": status,
                "trusted": status == "healthy",
            }
        )
    return sorted(ledgers, key=lambda row: (row["source"], row["pricing_basis"], row["currency"]))


def build_portfolio(
    evidence: Sequence[Mapping[str, Any]],
    *,
    source_health: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Build application summaries from canonical evidence."""

    exact = [
        row
        for row in evidence
        if row.get("application_key")
        and row.get("attribution_method") in EXACT_ATTRIBUTION_METHODS
    ]
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in exact:
        grouped[str(row["application_key"])].append(row)

    summaries = []
    for application_key, rows in grouped.items():
        display_values = [
            str(row.get("raw_application"))
            for row in rows
            if row.get("raw_application")
        ]
        display_name = (
            Counter(display_values).most_common(1)[0][0]
            if display_values
            else application_key
        )
        related_conflicts = [
            row
            for row in evidence
            if application_key in (row.get("conflict_values") or [])
            and row.get("cost_known", True)
        ]
        matched = 0
        missing = 0
        row_tag_conflicts = 0
        for row in rows:
            identity_tags = _json_mapping(row.get("identity_tags"))
            normalized_tag_values = {
                value
                for raw_value in identity_tags.values()
                if (value := normalize_application_key(raw_value))
            }
            if not normalized_tag_values:
                missing += 1
            elif normalized_tag_values == {application_key}:
                matched += 1
            else:
                row_tag_conflicts += 1
        cost_rows = [row for row in rows if row.get("cost_known", True)]
        azure_groups = {
            row.get("resource_group")
            for row in cost_rows
            if row.get("source") == "azure" and row.get("resource_group")
        }
        related_unresolved = [
            row
            for row in evidence
            if row.get("source") == "azure"
            and row.get("resource_group") in azure_groups
            and row.get("attribution_method")
            in {"SHARED_UNALLOCATED", "UNATTRIBUTED"}
            and row.get("cost_known", True)
        ]
        denominator = len(cost_rows) + len(related_conflicts) + len(related_unresolved)
        ledgers = _ledger_rows(rows, evidence, source_health)
        unhealthy = any(ledger["status"] != "healthy" for ledger in ledgers)
        summaries.append(
            {
                "application_key": application_key,
                "display_name": display_name,
                "environments": sorted(
                    {str(row.get("environment")) for row in rows if row.get("environment")}
                ),
                "sources": sorted(
                    {str(row.get("source")) for row in rows if row.get("source")}
                ),
                "ledgers": ledgers,
                "trend_pct": _trend_pct(cost_rows) if len(ledgers) == 1 else None,
                "tag_health": {
                    "status": (
                        "conflict"
                        if related_conflicts or row_tag_conflicts
                        else ("missing" if missing else "matched")
                    ),
                    "matched": matched,
                    "missing": missing,
                    "conflicts": len(related_conflicts) + row_tag_conflicts,
                },
                "coverage_pct": (
                    round(len(cost_rows) / denominator * 100, 1)
                    if denominator and not unhealthy
                    else None
                ),
                "last_evidence_at": _iso_max(
                    row.get("evidence_at") for row in rows
                ),
            }
        )
    return sorted(summaries, key=lambda row: row["display_name"].casefold())


def build_profile(
    application_key: str,
    evidence: Sequence[Mapping[str, Any]],
    *,
    source_health: Sequence[Mapping[str, Any]] = (),
    days: int = 30,
    today: date | None = None,
) -> dict[str, Any] | None:
    """Build the application profile while retaining unallocated source pools."""

    key = normalize_application_key(application_key)
    exact_rows = [
        row
        for row in evidence
        if row.get("application_key") == key
        and row.get("attribution_method") in EXACT_ATTRIBUTION_METHODS
    ]
    if not exact_rows:
        return None
    summary = next(
        row
        for row in build_portfolio(evidence, source_health=source_health)
        if row["application_key"] == key
    )
    today = today or date.today()
    start = today.fromordinal(today.toordinal() - days + 1)

    series_groups: dict[tuple[str, str], float] = defaultdict(float)
    cost_rows = [row for row in exact_rows if row.get("cost_known", True)]
    for row in cost_rows:
        ledger_id = _ledger_id(
            str(row.get("source") or ""),
            str(row.get("pricing_basis") or ""),
            str(row.get("currency") or ""),
        )
        series_groups[(str(row.get("usage_date") or ""), ledger_id)] += float(
            row.get("cost") or 0
        )
    ledger_lookup = {row["id"]: row for row in summary["ledgers"]}
    series = [
        {
            "usage_date": usage_date,
            "ledger_id": ledger_id,
            "source": ledger_lookup[ledger_id]["source"],
            "currency": ledger_lookup[ledger_id]["currency"],
            "pricing_basis": ledger_lookup[ledger_id]["pricing_basis"],
            "cost": round(cost, 4),
        }
        for (usage_date, ledger_id), cost in sorted(series_groups.items())
    ]

    driver_groups: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in cost_rows:
        group_key = (
            str(row.get("source") or ""),
            str(row.get("pricing_basis") or ""),
            str(row.get("currency") or ""),
            str(row.get("resource_type") or ""),
            str(row.get("resource_id") or ""),
            str(row.get("resource_name") or ""),
            str(row.get("service") or ""),
            str(row.get("workload") or ""),
        )
        driver = driver_groups.setdefault(
            group_key,
            {
                "source": group_key[0],
                "ledger_id": _ledger_id(*group_key[:3]),
                "dimension": "resource",
                "name": group_key[5] or group_key[4] or group_key[6],
                "resource_type": group_key[3],
                "resource_id": group_key[4],
                "service": group_key[6],
                "workload": group_key[7],
                "path": [
                    {
                        "dimension": dimension,
                        "key": value,
                        "label": label,
                    }
                    for dimension, value, label in (
                        ("source", group_key[0], group_key[0]),
                        ("service", group_key[6], group_key[6] or "Unspecified service"),
                        (
                            "resource",
                            group_key[4],
                            group_key[5] or group_key[4] or "Unidentified resource",
                        ),
                        ("workload", group_key[7], group_key[7]),
                    )
                    if value
                ],
                "cost": 0.0,
                "currency": group_key[2],
                "pricing_basis": group_key[1],
                "attribution_method": str(row.get("attribution_method") or ""),
            },
        )
        driver["cost"] += float(row.get("cost") or 0)
    drivers = sorted(
        ({**row, "cost": round(row["cost"], 4)} for row in driver_groups.values()),
        key=lambda row: abs(row["cost"]),
        reverse=True,
    )

    alignment_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    exact_azure_groups = {
        row.get("resource_group")
        for row in cost_rows
        if row.get("source") == "azure" and row.get("resource_group")
    }
    for row in evidence:
        conflicts = set(row.get("conflict_values") or [])
        same_shared_group = (
            row.get("source") == "azure"
            and row.get("resource_group") in exact_azure_groups
            and row.get("attribution_method")
            in {"SHARED_UNALLOCATED", "UNATTRIBUTED"}
        )
        if (
            row.get("application_key") != key
            and key not in conflicts
            and not same_shared_group
        ):
            continue
        observations = [
            dict(item) for item in (row.get("tag_observations") or [])
        ]
        if not observations:
            tags = _json_mapping(row.get("identity_tags"))
            if row.get("tag_key") and row.get("raw_application"):
                tags.setdefault(str(row["tag_key"]), row["raw_application"])
            observations = [
                {
                    "tag_key": tag_key,
                    "tag_value": raw_value,
                    "normalized_value": normalize_application_key(raw_value),
                    "observed_cost": None,
                    "evidence_id": None,
                }
                for tag_key, raw_value in sorted(tags.items())
            ]
        if not observations:
            observations = [
                {
                    "tag_key": None,
                    "tag_value": None,
                    "normalized_value": None,
                    "observed_cost": None,
                    "evidence_id": None,
                }
            ]
        for observation in observations:
            tag_key = observation.get("tag_key")
            raw_value = observation.get("tag_value")
            normalized = (
                observation.get("normalized_value")
                or normalize_application_key(raw_value)
            )
            if row.get("attribution_method") == "CONFLICT":
                status = "conflict"
            elif normalized is None:
                status = "missing"
            else:
                status = "matched" if normalized == key else "conflict"
            alignment_key = (
                row.get("source"),
                row.get("resource_id"),
                tag_key,
                str(raw_value) if raw_value is not None else None,
                status,
            )
            candidate = {
                "source": row.get("source"),
                "resource_id": row.get("resource_id"),
                "resource_name": row.get("resource_name"),
                "tag_key": tag_key,
                "raw_value": (
                    str(raw_value) if raw_value is not None else None
                ),
                "normalized_value": normalized,
                "status": status,
                "observed_cost": observation.get("observed_cost"),
                "evidence_id": observation.get("evidence_id"),
                "scope": str(row.get("scope") or ""),
                "freshness": row.get("evidence_at"),
            }
            existing = alignment_by_key.get(alignment_key)
            if existing is None or str(candidate.get("freshness") or "") > str(
                existing.get("freshness") or ""
            ):
                alignment_by_key[alignment_key] = candidate
    tag_alignment = sorted(
        alignment_by_key.values(),
        key=lambda row: (
            str(row.get("source") or ""),
            str(row.get("resource_name") or ""),
            str(row.get("tag_key") or ""),
            str(row.get("raw_value") or ""),
        ),
    )

    coverage = []
    source_groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in evidence:
        source_groups[
            (
                str(row.get("source") or ""),
                str(row.get("pricing_basis") or "UNRESOLVED"),
                str(row.get("currency") or "UNRESOLVED"),
            )
        ].append(row)
    unallocated = []
    azure_resource_groups = {
        str(row.get("resource_group"))
        for row in exact_rows
        if str(row.get("source") or "").casefold() == "azure"
        and row.get("resource_group")
    }
    for (source, basis, currency), rows in source_groups.items():
        attributed_for_application = [
            row
            for row in rows
            if row.get("application_key") == key
            and row.get("attribution_method") in EXACT_ATTRIBUTION_METHODS
            and row.get("cost_known", True)
        ]
        conflicts_for_application = [
            row
            for row in rows
            if row.get("attribution_method") == "CONFLICT"
            and key in (row.get("conflict_values") or [])
            and row.get("cost_known", True)
        ]
        unresolved_for_application = [
            row
            for row in rows
            if source.casefold() == "azure"
            and row.get("resource_group") in exact_azure_groups
            and row.get("attribution_method")
            in {"SHARED_UNALLOCATED", "UNATTRIBUTED"}
            and row.get("cost_known", True)
        ]
        attributed_cost = sum(
            float(row.get("cost") or 0) for row in attributed_for_application
        )
        conflict_cost = sum(
            float(row.get("cost") or 0) for row in conflicts_for_application
        )
        unresolved_cost = sum(
            float(row.get("cost") or 0) for row in unresolved_for_application
        )
        denominator_cost = (
            abs(attributed_cost) + abs(conflict_cost) + abs(unresolved_cost)
        )
        source_status = _ledger_source_status(
            source,
            attributed_for_application,
            source_health,
        )
        coverage.append(
            {
                "source": source,
                "status": (
                    source_status
                    if source_status != "healthy"
                    else ("conflict" if conflicts_for_application else "healthy")
                ),
                "attributed_rows": len(attributed_for_application),
                "total_rows": (
                    len(attributed_for_application)
                    + len(conflicts_for_application)
                    + len(unresolved_for_application)
                ),
                "attributed_cost": round(attributed_cost, 4),
                "total_cost": round(
                    attributed_cost + conflict_cost + unresolved_cost,
                    4,
                ),
                "currency": currency,
                "pricing_basis": basis,
                "coverage_pct": (
                    round(abs(attributed_cost) / denominator_cost * 100, 1)
                    if denominator_cost and source_status == "healthy"
                    else None
                ),
            }
        )
        by_reason: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            method = str(row.get("attribution_method") or "UNATTRIBUTED")
            if method in EXACT_ATTRIBUTION_METHODS:
                continue
            if method == "CONFLICT" and key in (row.get("conflict_values") or []):
                by_reason[method].append(row)
                continue
            if (
                source.casefold() == "azure"
                and row.get("resource_group") in azure_resource_groups
            ):
                by_reason[f"{method}:resource-group:{row.get('resource_group')}"].append(
                    row
                )
        for reason, reason_rows in by_reason.items():
            unallocated.append(
                {
                    "source": source,
                    "reason": reason,
                    "cost": round(
                        sum(float(row.get("cost") or 0) for row in reason_rows),
                        4,
                    ),
                    "currency": currency,
                    "pricing_basis": basis,
                    "row_count": len(reason_rows),
                }
            )

    return {
        "application": {
            key: value
            for key, value in summary.items()
            if key
            in {
                "application_key",
                "display_name",
                "environments",
                "sources",
                "last_evidence_at",
            }
        },
        "period": {
            "window": f"{days}d",
            "days": days,
            "start": start.isoformat(),
            "end": today.isoformat(),
        },
        "ledgers": summary["ledgers"],
        "series": series,
        "drivers": drivers,
        "tag_alignment": tag_alignment,
        "coverage": sorted(
            coverage, key=lambda row: (row["source"], row["pricing_basis"], row["currency"])
        ),
        "unallocated": sorted(
            unallocated,
            key=lambda row: (row["source"], row["pricing_basis"], row["currency"], row["reason"]),
        ),
        "source_health": [
            {
                **dict(row),
                "scope": str(row.get("scope") or _source_scope(row)),
                "freshness": (
                    row.get("freshness")
                    or row.get("last_success_at")
                    or row.get("observed_at")
                ),
            }
            for row in source_health
        ],
    }


def classify_application_findings(
    evidence: Sequence[Mapping[str, Any]],
    source_health: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Normalize attribution conflicts, missing tags, and unhealthy sources."""

    conflicts = []
    for row in evidence:
        if row.get("attribution_method") != "CONFLICT":
            continue
        conflicts.append(
            {
                "resource_id": str(row.get("resource_id") or row.get("evidence_id")),
                "name": str(row.get("resource_name") or row.get("resource_id") or ""),
                "resource_type": str(row.get("resource_type") or "resource"),
                "reason": (
                    "Application identity evidence conflicts: "
                    + ", ".join(str(value) for value in row.get("conflict_values") or [])
                ),
                "action": "review",
                "severity": "HIGH",
                "confidence": 1.0,
                "currency": row.get("currency"),
                "pricing_basis": row.get("pricing_basis"),
                "observed_cost": row.get("cost"),
                "scope": row.get("scope"),
                "evidence_refs": row.get("evidence_refs") or [],
                "freshness": row.get("evidence_at"),
            }
        )

    exact_azure_groups = {
        str(row.get("resource_group"))
        for row in evidence
        if row.get("source") == "azure"
        and row.get("application_key")
        and row.get("attribution_method") in EXACT_ATTRIBUTION_METHODS
        and row.get("resource_group")
    }
    missing_tags = [
        {
            "resource_id": str(row.get("resource_id") or row.get("evidence_id")),
            "name": str(row.get("resource_name") or row.get("resource_id") or ""),
            "resource_type": str(row.get("resource_type") or "azure_resource"),
            "reason": (
                "A shared Azure resource has no reconciled application identity "
                "tag and remains outside every exact application total."
            ),
            "action": "review",
            "severity": "MEDIUM",
            "confidence": 1.0,
            "currency": row.get("currency"),
            "pricing_basis": row.get("pricing_basis"),
            "observed_cost": row.get("cost"),
            "scope": row.get("scope"),
            "evidence_refs": row.get("evidence_refs") or [],
            "freshness": row.get("evidence_at"),
        }
        for row in evidence
        if row.get("source") == "azure"
        and str(row.get("resource_group") or "") in exact_azure_groups
        and row.get("attribution_method")
        in {"SHARED_UNALLOCATED", "UNATTRIBUTED"}
    ]
    unhealthy_sources = [
        {
            "resource_id": f"application-source:{row.get('source')}",
            "name": str(row.get("source") or "application evidence"),
            "resource_type": "evidence_source",
            "reason": str(row.get("notes") or "Application evidence source is unhealthy."),
            "action": "review",
            "severity": (
                "HIGH"
                if str(row.get("status") or "").casefold()
                in {"unavailable", "truncated"}
                else "MEDIUM"
            ),
            "confidence": 1.0,
            "status": row.get("status"),
            "scope": row.get("scope") or _source_scope(row),
            "freshness": (
                row.get("freshness")
                or row.get("last_success_at")
                or row.get("observed_at")
            ),
        }
        for row in source_health
        if str(row.get("status") or "").casefold() != "healthy"
    ]
    return {
        "governance/application-attribution-conflict": conflicts,
        "governance/application-tag-missing": missing_tags,
        "governance/application-source-health": unhealthy_sources,
    }


def encode_cursor(offset: int) -> str:
    payload = json.dumps({"offset": max(0, int(offset))}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        offset = int(payload["offset"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid pagination cursor.") from exc
    if offset < 0:
        raise ValueError("Invalid pagination cursor.")
    return offset


def paginate(
    rows: Sequence[Mapping[str, Any]],
    *,
    cursor: str | None,
    limit: int,
) -> tuple[list[dict[str, Any]], str | None]:
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200")
    offset = decode_cursor(cursor)
    page = [dict(row) for row in rows[offset : offset + limit]]
    next_cursor = (
        encode_cursor(offset + limit) if offset + limit < len(rows) else None
    )
    return page, next_cursor


def read_databricks_rows(
    w: WorkspaceClient,
    warehouse_id: str,
    days: int,
    *,
    workspace_id: str,
    environment: str,
) -> list[dict[str, Any]]:
    rows = run_query(
        w,
        load_query("application_cost_databricks"),
        warehouse_id,
        {"days": max(0, int(days) - 1), "workspace_id": workspace_id},
        row_limit=_APPLICATION_EVIDENCE_ROW_LIMIT + 1,
    )
    if len(rows) > _APPLICATION_EVIDENCE_ROW_LIMIT:
        raise EvidenceTruncatedError(
            "Databricks billing evidence exceeded the exact-read limit."
        )
    return [
        {
            **row,
            "workspace_id": workspace_id,
            "environment": environment,
            "source": "databricks",
            "currency": "USD",
            "pricing_basis": "DATABRICKS_LIST",
            "scope": f"workspace:{workspace_id}",
            "shared_scope": False,
        }
        for row in rows
    ]


def read_azure_rows_sql(catalog: str, schema: str) -> str:
    """Current-scope resource/meter actuals; tags are joined in pure logic."""

    coarse = f"{catalog}.{schema}.azure_costs"
    detail = f"{catalog}.{schema}.azure_cost_details"
    return (
        "WITH current_scope AS ("
        "SELECT subscription_id, scope_filter "
        f"FROM {coarse} WHERE workspace_id = :workspace_id "
        "AND environment = :environment AND COALESCE(scope_filter, '') <> '' "
        "ORDER BY ingested_at DESC LIMIT 1"
        ") SELECT c.workspace_id, c.environment, c.subscription_id, "
        "c.scope_filter, c.usage_date, c.resource_id, c.resource_group, "
        "c.resource_type, c.meter_name, c.service_bucket, c.cost, c.currency, "
        "c.ingested_at "
        f"FROM {detail} c INNER JOIN current_scope s "
        "ON c.subscription_id = s.subscription_id AND c.scope_filter = s.scope_filter "
        "WHERE c.workspace_id = :workspace_id AND c.environment = :environment "
        "AND c.usage_date >= DATE_SUB(CURRENT_DATE(), :days)"
    )


def read_azure_resource_evidence_sql(catalog: str, schema: str) -> str:
    """Rows from the latest complete snapshot covering each requested day."""

    table = f"{catalog}.{schema}.azure_cost_resource_evidence"
    snapshots = f"{catalog}.{schema}.azure_cost_evidence_snapshots"
    return (
        "WITH exploded_snapshots AS (SELECT s.snapshot_id, s.observed_at, "
        "s.job_id, s.run_id, s.trigger_type, "
        "EXPLODE(SEQUENCE(s.query_start, s.query_end, INTERVAL 1 DAY)) AS usage_date "
        f"FROM {snapshots} s WHERE s.workspace_id = :workspace_id "
        "AND s.environment = :environment AND s.subscription_id = :subscription_id "
        "AND s.scope_filter = 'subscription' AND s.baseline_status = 'complete' "
        "AND s.query_end >= DATE_SUB(CURRENT_DATE(), :days)"
        "), snapshot_days AS (SELECT * FROM exploded_snapshots "
        "WHERE usage_date >= DATE_SUB(CURRENT_DATE(), :days) "
        "AND usage_date <= CURRENT_DATE()), ranked_days AS ("
        "SELECT *, ROW_NUMBER() OVER (PARTITION BY usage_date "
        "ORDER BY observed_at DESC, snapshot_id DESC) AS rn FROM snapshot_days"
        "), selected AS (SELECT snapshot_id, usage_date, job_id, run_id, trigger_type "
        "FROM ranked_days WHERE rn = 1) "
        f"SELECT e.*, s.job_id, s.run_id, s.trigger_type FROM {table} e "
        "INNER JOIN selected s "
        "ON e.snapshot_id = s.snapshot_id AND e.usage_date = s.usage_date"
    )


def read_azure_tag_rows_sql(catalog: str, schema: str) -> str:
    """Tag rows only from matching complete baseline/tag snapshots."""

    table = f"{catalog}.{schema}.azure_cost_tag_evidence"
    snapshots = f"{catalog}.{schema}.azure_cost_evidence_snapshots"
    return (
        "WITH exploded_snapshots AS (SELECT s.snapshot_id, s.observed_at, "
        "s.job_id, s.run_id, s.trigger_type, "
        "EXPLODE(SEQUENCE(s.query_start, s.query_end, INTERVAL 1 DAY)) AS usage_date "
        f"FROM {snapshots} s WHERE s.workspace_id = :workspace_id "
        "AND s.environment = :environment AND s.subscription_id = :subscription_id "
        "AND s.scope_filter = 'subscription' AND s.baseline_status = 'complete' "
        "AND s.tag_status = 'complete' AND s.tag_keys = :tag_keys "
        "AND s.query_end >= DATE_SUB(CURRENT_DATE(), :days)"
        "), snapshot_days AS (SELECT * FROM exploded_snapshots "
        "WHERE usage_date >= DATE_SUB(CURRENT_DATE(), :days) "
        "AND usage_date <= CURRENT_DATE()), ranked_days AS ("
        "SELECT *, ROW_NUMBER() OVER (PARTITION BY usage_date "
        "ORDER BY observed_at DESC, snapshot_id DESC) AS rn FROM snapshot_days"
        "), selected AS (SELECT snapshot_id, usage_date, job_id, run_id, trigger_type "
        "FROM ranked_days WHERE rn = 1) "
        f"SELECT e.*, s.job_id, s.run_id, s.trigger_type FROM {table} e "
        "INNER JOIN selected s "
        "ON e.snapshot_id = s.snapshot_id AND e.usage_date = s.usage_date"
    )


def read_bindings_sql(catalog: str, schema: str) -> str:
    table = f"{catalog}.{schema}.application_resource_bindings"
    snapshots = f"{catalog}.{schema}.application_binding_snapshots"
    return (
        "WITH app_snapshots AS (SELECT DISTINCT b.application_key, "
        "b.snapshot_id, b.observed_at "
        f"FROM {table} b INNER JOIN {snapshots} m ON b.snapshot_id = m.snapshot_id "
        "WHERE b.workspace_id = :workspace_id AND b.environment = :environment"
        "), ordered_snapshots AS (SELECT application_key, snapshot_id, "
        "observed_at AS effective_from, LEAD(observed_at) OVER ("
        "PARTITION BY application_key ORDER BY observed_at, snapshot_id"
        ") AS effective_to FROM app_snapshots"
        ") SELECT b.*, s.effective_from, s.effective_to, "
        "m.job_id, m.run_id, m.trigger_type "
        f"FROM {table} b INNER JOIN ordered_snapshots s "
        "ON b.snapshot_id = s.snapshot_id AND b.application_key = s.application_key "
        f"INNER JOIN {snapshots} m ON b.snapshot_id = m.snapshot_id "
        "WHERE b.workspace_id = :workspace_id AND b.environment = :environment "
        "AND s.effective_from <= CURRENT_TIMESTAMP() "
        "AND (s.effective_to IS NULL OR "
        "s.effective_to >= DATE_SUB(CURRENT_TIMESTAMP(), :days))"
    )


def read_source_health_sql(catalog: str, schema: str) -> str:
    table = f"{catalog}.{schema}.application_source_health"
    return (
        "WITH scoped AS (SELECT h.* "
        f"FROM {table} h WHERE workspace_id = :workspace_id "
        "AND environment = :environment "
        "AND (LOWER(source) NOT LIKE 'azure%' OR "
        "(subscription_id = :subscription_id AND scope_filter = 'subscription'))"
        "), ranked AS (SELECT h.*, "
        "MIN(coverage_start) OVER (PARTITION BY source) AS retained_coverage_start, "
        "MAX(coverage_end) OVER (PARTITION BY source) AS retained_coverage_end, "
        "ROW_NUMBER() OVER (PARTITION BY source "
        "ORDER BY observed_at DESC, evidence_id DESC) AS rn "
        "FROM scoped h) "
        "SELECT source, status, last_success_at, retained_coverage_start AS coverage_start, "
        "retained_coverage_end AS coverage_end, notes, subscription_id, scope_filter, "
        "observed_at "
        "FROM ranked WHERE rn = 1 ORDER BY source"
    )


def _source_scope(row: Mapping[str, Any]) -> str:
    scope_filter = str(row.get("scope_filter") or "")
    subscription_id = str(row.get("subscription_id") or "")
    if subscription_id and scope_filter == "subscription":
        return f"subscription:{subscription_id}"
    return scope_filter


def _normalize_source_health(
    row: Mapping[str, Any],
    *,
    current_time: datetime,
) -> dict[str, Any]:
    normalized = dict(row)
    freshness = (
        normalized.get("last_success_at")
        or normalized.get("observed_at")
        or None
    )
    normalized["scope"] = _source_scope(normalized)
    normalized["freshness"] = freshness
    status = str(normalized.get("status") or "unavailable").casefold()
    if status in {"healthy", "partial"} and freshness:
        try:
            observed = datetime.fromisoformat(str(freshness).replace("Z", "+00:00"))
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=UTC)
            stale_before = current_time - timedelta(
                days=_SOURCE_FRESHNESS_GRACE_DAYS
            )
            coverage_end = str(normalized.get("coverage_end") or "")[:10]
            coverage_is_stale = bool(
                coverage_end
                and coverage_end
                < (current_time.date() - timedelta(days=_SOURCE_FRESHNESS_GRACE_DAYS)).isoformat()
            )
            if observed < stale_before or coverage_is_stale:
                normalized["status"] = "stale"
                normalized["notes"] = (
                    f"{normalized.get('notes') or ''} Evidence is older than "
                    f"{_SOURCE_FRESHNESS_GRACE_DAYS} days."
                ).strip()
        except ValueError:
            normalized["status"] = "partial"
    return normalized


def _costs_reconcile(baseline: float, observed: float) -> bool:
    tolerance = max(0.01, abs(baseline) * 0.000001)
    return abs(baseline - observed) <= tolerance


def read_application_evidence(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    *,
    workspace_id: str,
    environment: str,
    subscription_id: str,
    days: int,
    tag_keys: str | Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read each source independently and return partial evidence on failures."""

    current_time = datetime.now(UTC)
    configured_keys = parse_application_tag_keys(tag_keys)
    query_params = {
        "days": max(0, int(days) - 1),
        "workspace_id": workspace_id,
        "environment": environment,
        "subscription_id": subscription_id,
    }
    raw_rows: list[dict[str, Any]] = []
    health_by_source: dict[str, dict[str, Any]] = {}

    def unavailable(source: str, error: Exception) -> None:
        is_azure = source.startswith("azure")
        health_by_source[source] = {
            "source": source,
            "status": (
                "truncated"
                if isinstance(error, EvidenceTruncatedError)
                else "unavailable"
            ),
            "last_success_at": None,
            "coverage_start": None,
            "coverage_end": None,
            "notes": f"{source} could not be read ({type(error).__name__}).",
            "subscription_id": subscription_id if is_azure else "",
            "scope_filter": (
                "subscription"
                if is_azure
                else (f"workspace:{workspace_id}" if source.startswith("databricks") else "")
            ),
            "observed_at": current_time.isoformat(),
        }

    try:
        for row in run_query(
            w,
            read_source_health_sql(catalog, schema),
            warehouse_id,
            {
                "workspace_id": workspace_id,
                "environment": environment,
                "subscription_id": subscription_id,
            },
            row_limit=20,
        ):
            health_by_source[str(row.get("source") or "unknown")] = dict(row)
    except Exception as exc:  # noqa: BLE001 - other sources remain useful
        unavailable("application_source_health", exc)

    try:
        databricks_rows = read_databricks_rows(
            w,
            warehouse_id,
            days,
            workspace_id=workspace_id,
            environment=environment,
        )
        raw_rows.extend(databricks_rows)
        unpriced_quantity = sum(
            float(row.get("unpriced_usage_quantity") or 0)
            for row in databricks_rows
        )
        health_by_source["databricks_billing"] = {
            "source": "databricks_billing",
            "status": "partial" if unpriced_quantity else "healthy",
            "last_success_at": current_time.isoformat(),
            "coverage_start": (
                current_time.date() - timedelta(days=max(0, days - 1))
            ).isoformat(),
            "coverage_end": current_time.date().isoformat(),
            "notes": (
                f"{unpriced_quantity:g} usage units had no effective list price "
                "and were excluded from exact USD amounts."
                if unpriced_quantity
                else "All returned Databricks usage had an effective list price."
            ),
            "subscription_id": "",
            "scope_filter": f"workspace:{workspace_id}",
            "observed_at": current_time.isoformat(),
        }
    except Exception as exc:  # noqa: BLE001 - Azure can still render
        unavailable("databricks_billing", exc)

    try:
        bindings = run_query(
            w,
            read_bindings_sql(catalog, schema),
            warehouse_id,
            {
                "workspace_id": workspace_id,
                "environment": environment,
                "days": query_params["days"],
            },
            row_limit=_BINDING_EVIDENCE_ROW_LIMIT + 1,
        )
        if len(bindings) > _BINDING_EVIDENCE_ROW_LIMIT:
            raise EvidenceTruncatedError(
                "Databricks App binding evidence exceeded the exact-read limit."
            )
        # Seed zero-spend Apps from the current complete inventory snapshot.
        seen_apps = set()
        for binding in bindings:
            if (
                binding.get("resource_type") == "app"
                and not binding.get("effective_to")
                and binding.get("application_key") not in seen_apps
            ):
                seen_apps.add(binding.get("application_key"))
                raw_rows.append(
                    {
                        "workspace_id": workspace_id,
                        "environment": environment,
                        "usage_date": str(binding.get("observed_at") or "")[:10],
                        "source": "databricks",
                        "resource_type": "app",
                        "resource_id": binding.get("resource_id"),
                        "resource_name": binding.get("raw_application"),
                        "metadata_application": binding.get("raw_application"),
                        "service": "Databricks Apps inventory",
                        "workload": "APP_INVENTORY",
                        "cost": 0,
                        "currency": "USD",
                        "pricing_basis": "DATABRICKS_LIST",
                        "evidence_at": binding.get("observed_at"),
                        "scope": f"workspace:{workspace_id}",
                        "inventory_only": True,
                        "cost_known": False,
                        "snapshot_id": binding.get("snapshot_id"),
                        "evidence_refs": [binding.get("evidence_id")],
                        "job_id": binding.get("job_id"),
                        "run_id": binding.get("run_id"),
                        "trigger_type": binding.get("trigger_type"),
                    }
                )
    except Exception as exc:  # noqa: BLE001 - billing evidence remains useful
        bindings = []
        unavailable("databricks_app_bindings", exc)

    if not subscription_id:
        azure_baseline: list[dict[str, Any]] = []
        tag_rows: list[dict[str, Any]] = []
        for source in ("azure_cost_resources", "azure_cost_tags"):
            health_by_source[source] = {
                "source": source,
                "status": "not_configured",
                "last_success_at": None,
                "coverage_start": None,
                "coverage_end": None,
                "notes": "Azure subscription ID is not configured.",
                "subscription_id": "",
                "scope_filter": "",
                "observed_at": current_time.isoformat(),
            }
    else:
        try:
            azure_baseline = run_query(
                w,
                read_azure_resource_evidence_sql(catalog, schema),
                warehouse_id,
                query_params,
                row_limit=_APPLICATION_EVIDENCE_ROW_LIMIT + 1,
            )
            if len(azure_baseline) > _APPLICATION_EVIDENCE_ROW_LIMIT:
                raise EvidenceTruncatedError(
                    "Azure resource evidence exceeded the exact-read limit."
                )
        except Exception as exc:  # noqa: BLE001 - Databricks can still render
            azure_baseline = []
            unavailable("azure_cost_resources", exc)
        try:
            tag_rows = run_query(
                w,
                read_azure_tag_rows_sql(catalog, schema),
                warehouse_id,
                {**query_params, "tag_keys": ",".join(configured_keys)},
                row_limit=_APPLICATION_EVIDENCE_ROW_LIMIT + 1,
            )
            if len(tag_rows) > _APPLICATION_EVIDENCE_ROW_LIMIT:
                raise EvidenceTruncatedError(
                    "Azure tag evidence exceeded the exact-read limit."
                )
        except Exception as exc:  # noqa: BLE001 - baseline stays unallocated
            tag_rows = []
            unavailable("azure_cost_tags", exc)

    tag_observations: dict[
        tuple[str, str, str, str], dict[str, list[Mapping[str, Any]]]
    ] = defaultdict(lambda: defaultdict(list))
    for row in tag_rows:
        tag_key = str(row.get("tag_key") or "").casefold()
        if tag_key in configured_keys:
            tag_observations[
                (
                    str(row.get("usage_date") or "")[:10],
                    str(row.get("resource_id") or "").casefold(),
                    str(row.get("currency") or "UNRESOLVED").upper(),
                    str(row.get("snapshot_id") or ""),
                )
            ][tag_key].append(row)

    # The resource query is the one Azure money source. Tag queries supply
    # identity only when their complete observation timestamp matches it.
    for baseline in azure_baseline:
        observation_key = (
            str(baseline.get("usage_date") or "")[:10],
            str(baseline.get("resource_id") or "").casefold(),
            str(baseline.get("currency") or "UNRESOLVED").upper(),
            str(baseline.get("snapshot_id") or ""),
        )
        observations = tag_observations.get(observation_key, {})
        baseline_cost = float(baseline.get("cost") or 0)
        material_tolerance = max(0.01, abs(baseline_cost) * 0.000001)
        all_values = {
            normalized
            for key_rows in observations.values()
            for row in key_rows
            if abs(float(row.get("observed_cost") or 0)) > material_tolerance
            and (
                normalized := normalize_application_key(row.get("tag_value"))
            )
        }
        tags: dict[str, str] = {}
        force_conflict = len(all_values) > 1
        force_unresolved = False
        raw_tag_observations = []
        for key in configured_keys:
            key_rows = observations.get(key, [])
            key_cost = sum(float(row.get("observed_cost") or 0) for row in key_rows)
            if not key_rows or not _costs_reconcile(baseline_cost, key_cost):
                force_conflict = force_conflict or bool(all_values)
                force_unresolved = force_unresolved or not all_values
            material_rows = [
                row
                for row in key_rows
                if abs(float(row.get("observed_cost") or 0)) > material_tolerance
            ]
            normalized_values = {
                value
                for row in material_rows
                if (value := normalize_application_key(row.get("tag_value")))
            }
            has_material_blank = any(
                normalize_application_key(row.get("tag_value")) is None
                for row in material_rows
            )
            if len(normalized_values) > 1 or (
                normalized_values and has_material_blank
            ):
                force_conflict = True
            elif len(normalized_values) == 1 and not has_material_blank:
                chosen_value = next(iter(normalized_values))
                chosen_row = next(
                    row
                    for row in key_rows
                    if normalize_application_key(row.get("tag_value"))
                    == chosen_value
                )
                tags[key] = str(chosen_row.get("tag_value") or "")
            for tag_row in key_rows:
                raw_tag_observations.append(
                    {
                        "evidence_id": str(tag_row.get("evidence_id") or ""),
                        "tag_key": key,
                        "tag_value": str(tag_row.get("tag_value") or ""),
                        "normalized_value": normalize_application_key(
                            tag_row.get("tag_value")
                        ),
                        "observed_cost": float(tag_row.get("observed_cost") or 0),
                    }
                )
        tag_evidence_refs = [
            str(row.get("evidence_id"))
            for key_rows in observations.values()
            for row in key_rows
            if row.get("evidence_id")
        ]
        raw_rows.append(
            {
                **baseline,
                "workspace_id": workspace_id,
                "environment": environment,
                "source": "azure",
                "workload": "",
                "pricing_basis": "AZURE_ACTUAL",
                "tags": tags,
                "forced_conflict_values": sorted(all_values),
                "force_conflict": force_conflict,
                "force_unresolved": force_unresolved,
                "tag_observations": raw_tag_observations,
                "evidence_refs": tag_evidence_refs,
                "scope": (
                    f"subscription:{subscription_id};"
                    "metric:ActualCost/PreTaxCost"
                ),
                "shared_scope": True,
                "evidence_at": baseline.get("observed_at"),
            }
        )
    return (
        resolve_evidence_rows(raw_rows, bindings=bindings, tag_keys=configured_keys),
        sorted(
            (
                _normalize_source_health(row, current_time=current_time)
                for row in health_by_source.values()
            ),
            key=lambda row: str(row.get("source")),
        ),
    )

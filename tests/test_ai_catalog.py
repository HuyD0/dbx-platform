"""Offline tests for the unified AI model catalog (pure logic only)."""

import json

import pytest

from dbx_platform.ai_catalog import (
    AI_ACCOUNTS_QUERY,
    AI_DEPLOYMENTS_QUERY,
    AI_ROLE_ASSIGNMENTS_QUERY,
    azure_access_level,
    build_resource_graph_body,
    classify_ai_catalog,
    deployment_account_id,
    match_assignments_to_accounts,
    merge_access_sql,
    merge_catalog_sql,
    normalize_azure_accounts,
    normalize_azure_deployments,
    normalize_endpoint_acls,
    normalize_serving_entities,
    normalize_uc_grants,
    parse_subscriptions,
    store_access,
    store_catalog,
)

SUB = "ea936670-dda1-4884-8467-49c225bf3e83"
ACCOUNT_ID = (
    f"/subscriptions/{SUB}/resourcegroups/rg-ai/providers/"
    "microsoft.cognitiveservices/accounts/foundry-prod"
)


def _account(**overrides) -> dict:
    row = {
        "id": ACCOUNT_ID,
        "name": "foundry-prod",
        "location": "eastus2",
        "kind": "AIServices",
        "subscriptionId": SUB,
        "resourceGroup": "rg-ai",
        "skuName": "S0",
        "endpoint": "https://foundry-prod.cognitiveservices.azure.com/",
        "disableLocalAuth": True,
        "publicNetworkAccess": "Enabled",
        "tags": {"team": "ml"},
    }
    row.update(overrides)
    return row


# --- request construction -------------------------------------------------------

def test_query_constants_target_expected_tables():
    assert "microsoft.cognitiveservices/accounts'" in AI_ACCOUNTS_QUERY
    assert "microsoft.cognitiveservices/accounts/deployments" in AI_DEPLOYMENTS_QUERY
    assert "authorizationresources" in AI_ROLE_ASSIGNMENTS_QUERY
    assert "microsoft.authorization/roleassignments" in AI_ROLE_ASSIGNMENTS_QUERY
    assert "roledefinitions" in AI_ROLE_ASSIGNMENTS_QUERY


def test_parse_subscriptions_trims_and_drops_empties():
    assert parse_subscriptions(f" {SUB} , ,second-sub,") == [SUB, "second-sub"]
    assert parse_subscriptions("") == []
    assert parse_subscriptions(None) == []


def test_body_scopes_to_subscriptions_when_provided():
    body = build_resource_graph_body("resources", [SUB], authorization_scoped=True)
    assert body["subscriptions"] == [SUB]
    # Inherited MG assignments would be dropped by the default scope filter.
    assert body["options"]["authorizationScopeFilter"] == "AtScopeAboveAndBelow"
    assert body["options"]["resultFormat"] == "objectArray"


def test_body_omits_subscriptions_key_for_all_visible_mode():
    body = build_resource_graph_body("resources", [], authorization_scoped=True)
    assert "subscriptions" not in body
    assert "authorizationScopeFilter" not in body["options"]


def test_body_carries_skip_token():
    body = build_resource_graph_body("resources", [SUB], skip_token="tok123")
    assert body["options"]["$skipToken"] == "tok123"


# --- azure normalization --------------------------------------------------------

def test_deployment_account_id_derivation():
    assert deployment_account_id(f"{ACCOUNT_ID}/deployments/gpt-4o") == ACCOUNT_ID


def test_deployment_inherits_account_key_auth_and_region():
    accounts = [_account(disableLocalAuth=False)]
    accounts_by_id = {a["id"]: a for a in accounts}
    deployment = {
        "id": f"{ACCOUNT_ID}/deployments/gpt-4o",
        "name": "gpt-4o",
        "subscriptionId": SUB,
        "resourceGroup": "rg-ai",
        "modelName": "gpt-4o",
        "modelVersion": "2024-11-20",
        "modelFormat": "OpenAI",
        "skuName": "GlobalStandard",
        "skuCapacity": 100,
        "provisioningState": "Succeeded",
    }
    (row,) = normalize_azure_deployments([deployment], accounts_by_id)
    assert row["model_key"] == f"azure:{ACCOUNT_ID}/deployments/gpt-4o"
    assert row["entity_type"] == "DEPLOYMENT"
    assert row["model_name"] == "gpt-4o"
    assert row["provider"] == "OpenAI"
    assert row["region"] == "eastus2"
    assert row["endpoint_name"] == "foundry-prod"
    assert row["key_auth_enabled"] is True  # inherited: local auth NOT disabled


def test_account_key_auth_flag_inverts_disable_local_auth():
    secure, insecure = normalize_azure_accounts(
        [_account(), _account(disableLocalAuth=None)]
    )
    assert secure["key_auth_enabled"] is False
    assert insecure["key_auth_enabled"] is True
    assert secure["entity_type"] == "ACCOUNT"


# --- role-assignment scope matching ----------------------------------------------

def _assignment(scope: str, role: str = "Cognitive Services OpenAI User") -> dict:
    return {
        "id": f"{scope}/providers/microsoft.authorization/roleassignments/ra-1",
        "scope": scope,
        "principalId": "11111111-2222-3333-4444-555555555555",
        "principalType": "ServicePrincipal",
        "roleDefinitionId": "/providers/microsoft.authorization/roledefinitions/x",
        "roleName": role,
        "roleType": "BuiltInRole",
    }


def test_azure_access_level_table():
    assert azure_access_level("Cognitive Services OpenAI User") == "INVOKE"
    assert azure_access_level("cognitive services openai contributor") == "ADMIN"
    assert azure_access_level("Owner") == "ADMIN"
    assert azure_access_level("Azure AI Developer") == "INVOKE"
    assert azure_access_level("Cost Management Reader") == "READ"
    assert azure_access_level("Virtual Machine Contributor") == "OTHER"
    assert azure_access_level("") == "OTHER"


@pytest.mark.parametrize(
    ("scope", "expected_via"),
    [
        (ACCOUNT_ID, "DIRECT"),
        (f"{ACCOUNT_ID}/deployments/gpt-4o", "DIRECT"),
        (f"/subscriptions/{SUB}/resourcegroups/rg-ai", "RESOURCE_GROUP"),
        (f"/subscriptions/{SUB}", "SUBSCRIPTION"),
        ("/providers/microsoft.management/managementgroups/mg-root", "MANAGEMENT_GROUP"),
    ],
)
def test_scope_matching(scope, expected_via):
    rows = match_assignments_to_accounts([_assignment(scope)], [_account()])
    assert len(rows) == 1
    assert rows[0]["via"] == expected_via
    assert rows[0]["model_key"] == f"azure:{ACCOUNT_ID}"
    assert rows[0]["access_level"] == "INVOKE"


def test_other_subscription_and_rg_do_not_match():
    other_sub = _assignment("/subscriptions/00000000-0000-0000-0000-000000000000")
    other_rg = _assignment(f"/subscriptions/{SUB}/resourcegroups/rg-unrelated")
    assert match_assignments_to_accounts([other_sub, other_rg], [_account()]) == []


def test_unrelated_role_kept_only_when_directly_scoped():
    direct = _assignment(ACCOUNT_ID, role="Virtual Machine Contributor")
    inherited = _assignment(f"/subscriptions/{SUB}", role="Virtual Machine Contributor")
    rows = match_assignments_to_accounts([direct, inherited], [_account()])
    assert len(rows) == 1
    assert rows[0]["via"] == "DIRECT"
    assert rows[0]["access_level"] == "OTHER"


# --- databricks normalization -----------------------------------------------------

def test_uc_grant_execute_maps_to_invoke():
    rows = normalize_uc_grants(
        [
            {"full_name": "prod.ml.churn", "principal": "ml-team",
             "privileges": ["EXECUTE", "APPLY_TAG"]},
            {"full_name": "prod.ml.churn", "principal": "auditors",
             "privileges": ["APPLY_TAG"]},
        ]
    )
    assert rows[0]["access_level"] == "INVOKE"
    assert rows[1]["access_level"] == "OTHER"
    assert rows[0]["model_key"] == "uc:prod.ml.churn"


def test_endpoint_acl_picks_highest_level_and_inheritance():
    (row,) = normalize_endpoint_acls(
        [
            {
                "endpoint_name": "churn-api",
                "principal": "ml-team",
                "principal_type": "GROUP",
                "levels": [
                    {"level": "CAN_VIEW", "inherited": False},
                    {"level": "CAN_MANAGE", "inherited": True},
                ],
            }
        ]
    )
    assert row["access_level"] == "CAN_MANAGE"
    assert row["via"] == "INHERITED"
    assert row["model_key"] == "serving:churn-api"


def test_serving_entities_skip_system_endpoints():
    endpoints = [
        {"name": "databricks-claude", "is_system_endpoint": True,
         "served_entities": [{"entity_name": "claude"}]},
        {
            "name": "churn-api",
            "is_system_endpoint": False,
            "creator": "ml@example.com",
            "ready": "READY",
            "task": "llm/v1/chat",
            "has_usage_tracking": True,
            "served_entities": [
                {"entity_name": "prod.ml.churn", "entity_version": "3",
                 "is_external_or_fm": False, "workload_size": "Small",
                 "scale_to_zero": True},
            ],
        },
    ]
    (row,) = normalize_serving_entities(endpoints)
    assert row["model_key"] == "serving:churn-api/prod.ml.churn"
    assert row["entity_type"] == "CUSTOM_MODEL"
    assert row["model_version"] == "3"
    assert row["usage_tracking"] is True


# --- findings ---------------------------------------------------------------------

def test_key_auth_finding_only_for_current_accounts():
    rows = normalize_azure_accounts([_account(disableLocalAuth=False)])
    findings = classify_ai_catalog(rows, [])
    assert len(findings["ai-catalog/azure-key-auth"]) == 1
    assert findings["ai-catalog/azure-key-auth"][0]["severity"] == "HIGH"

    resolved = [{**rows[0], "is_current": False}]
    assert classify_ai_catalog(resolved, [])["ai-catalog/azure-key-auth"] == []


def test_broad_scope_role_dedupes_by_assignment():
    assignment = _assignment(f"/subscriptions/{SUB}")
    accounts = [_account(), _account(id=ACCOUNT_ID.replace("foundry-prod", "openai-2"),
                                     name="openai-2")]
    access = match_assignments_to_accounts([assignment], accounts)
    assert len(access) == 2  # one row per covered account
    findings = classify_ai_catalog([], access)
    assert len(findings["ai-catalog/azure-broad-scope-role"]) == 1


def test_uc_broad_grant_flagged_and_narrow_group_not():
    access = normalize_uc_grants(
        [
            {"full_name": "prod.ml.churn", "principal": "account users",
             "privileges": ["EXECUTE"]},
            {"full_name": "prod.ml.churn", "principal": "ml-team",
             "privileges": ["EXECUTE"]},
        ]
    )
    findings = classify_ai_catalog([], access)
    flagged = findings["ai-catalog/uc-model-broad-grant"]
    assert len(flagged) == 1
    assert flagged[0]["resource_id"] == "prod.ml.churn"


def test_endpoint_open_acl_boundary_can_view_not_flagged():
    acls = [
        {"endpoint_name": "churn-api", "principal": "users",
         "principal_type": "GROUP",
         "levels": [{"level": "CAN_VIEW", "inherited": False}]},
        {"endpoint_name": "open-api", "principal": "users",
         "principal_type": "GROUP",
         "levels": [{"level": "CAN_QUERY", "inherited": False}]},
    ]
    findings = classify_ai_catalog([], normalize_endpoint_acls(acls))
    flagged = findings["ai-catalog/endpoint-open-acl"]
    assert [f["name"] for f in flagged] == ["open-api"]


# --- merge/store ------------------------------------------------------------------

def test_merge_sql_soft_resolves_scoped_by_source():
    for sql in (merge_catalog_sql("main", "dbx_platform"),
                merge_access_sql("main", "dbx_platform")):
        assert "CREATE TABLE" not in sql
        assert "WHEN NOT MATCHED BY SOURCE" in sql
        assert "t.source = :source" in sql
        assert "t.is_current = false" in sql
        assert "THEN DELETE" not in sql
    assert "ai_model_catalog" in merge_catalog_sql("main", "dbx_platform")
    assert "ai_model_access" in merge_access_sql("main", "dbx_platform")
    assert "t.principal_id = s.principal_id" in merge_access_sql("main", "dbx_platform")


@pytest.mark.parametrize(
    ("writer", "table_fragment"),
    [(store_catalog, "ai_model_catalog"), (store_access, "ai_model_access")],
)
def test_store_reconciles_empty_snapshot_per_source(monkeypatch, writer, table_fragment):
    calls = []
    monkeypatch.setattr(
        "dbx_platform.ai_catalog.run_query",
        lambda _w, sql, _warehouse, params=None, **_kwargs: calls.append((sql, params))
        or [],
    )
    assert writer(
        object(),
        "warehouse",
        "main",
        "dbx_platform",
        [],
        workspace_id="w1",
        environment="prod",
        sources=["databricks_uc", "azure_openai"],
    ) == 0
    assert len(calls) == 2  # one merge per declared source, even when empty
    for sql, params in calls:
        assert table_fragment in sql
        assert json.loads(params["rows"]) == []
    assert {params["source"] for _sql, params in calls} == {
        "databricks_uc", "azure_openai",
    }


def test_store_rejects_rows_for_undeclared_source(monkeypatch):
    monkeypatch.setattr(
        "dbx_platform.ai_catalog.run_query",
        lambda *_args, **_kwargs: pytest.fail("invalid input must not write"),
    )
    rows = normalize_azure_accounts([_account()])
    with pytest.raises(ValueError, match="not declared as refreshed"):
        store_catalog(
            object(),
            "warehouse",
            "main",
            "dbx_platform",
            rows,
            workspace_id="w1",
            environment="prod",
            sources=["databricks_uc"],
        )


def test_store_failure_has_migration_guidance(monkeypatch):
    monkeypatch.setattr(
        "dbx_platform.ai_catalog.run_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(Exception("TABLE_NOT_FOUND")),
    )
    with pytest.raises(RuntimeError, match="schema_migrations"):
        store_catalog(
            object(),
            "warehouse",
            "main",
            "dbx_platform",
            [],
            workspace_id="w1",
            environment="prod",
            sources=["databricks_uc"],
        )

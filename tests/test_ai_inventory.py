"""Pure tests for the application-facing AI inventory explorer."""

import pytest

from dbx_platform.ai_inventory import (
    build_inventory_detail,
    build_inventory_page,
    is_system_model,
    normalize_inventory,
)


def _catalog(model_key: str, **overrides) -> dict:
    row = {
        "workspace_id": "123",
        "environment": "prod",
        "source": "databricks_uc",
        "model_key": model_key,
        "provider": "databricks",
        "model_name": model_key.removeprefix("uc:"),
        "entity_type": "REGISTERED_MODEL",
        "status": "READY",
        "owner": "ml@example.com",
        "key_auth_enabled": False,
        "details_json": "{}",
    }
    row.update(overrides)
    return row


def test_system_models_are_collapsed_out_of_the_default_view():
    rows = [
        _catalog("uc:system.ai.bge_base_en_v1_5"),
        _catalog("uc:prod.ml.churn"),
    ]

    assert is_system_model(rows[0])
    page = build_inventory_page(rows, [], [])

    assert page["summary"] == {
        "total": 2,
        "customer_managed": 1,
        "system": 1,
        "needs_attention": 0,
        "key_auth_exposed": 0,
        "groups_on_page": 1,
    }
    assert [row["model_key"] for row in page["items"]] == ["uc:prod.ml.churn"]


def test_risky_system_model_remains_visible_and_risks_include_broad_access():
    system = _catalog(
        "uc:system.ai.risky",
        key_auth_enabled=True,
        owner=None,
    )
    access = [{"model_key": system["model_key"], "principal_name": "account users"}]

    (normalized,) = normalize_inventory([system], access)
    assert normalized["ownership"] == "system"
    assert normalized["needs_attention"] is True
    assert normalized["risk_reasons"] == [
        "Key authentication enabled",
        "Broad user access",
    ]
    assert build_inventory_page([system], access, [])["items"][0]["model_key"] == system[
        "model_key"
    ]


def test_filters_search_facets_and_cursor_pagination_are_stable():
    rows = [
        _catalog(
            "azure:/subscriptions/s/accounts/a/deployments/gpt-4o",
            source="azure_openai",
            provider="OpenAI",
            model_name="gpt-4o",
            entity_type="DEPLOYMENT",
            endpoint_name="account-a",
            resource_group="rg-ai",
            resource_id="/subscriptions/s/accounts/a/deployments/gpt-4o",
            details_json='{"account_tags":{"project":"support"}}',
        ),
        _catalog("uc:prod.ml.churn", provider="MLflow"),
        _catalog("uc:prod.ml.fraud", provider="MLflow"),
    ]
    first = build_inventory_page(rows, [], [], view="all", limit=1)
    second = build_inventory_page(
        rows,
        [],
        [],
        view="all",
        limit=1,
        cursor=first["next_cursor"],
    )

    assert first["items"][0]["model_key"] != second["items"][0]["model_key"]
    searched = build_inventory_page(rows, [], [], query="support", view="all")
    assert [row["display_name"] for row in searched["items"]] == ["gpt-4o"]
    assert searched["items"][0]["tags"] == {"project": "support"}
    assert {item["value"] for item in searched["facets"]["source"]} == {
        "azure_openai",
        "databricks_uc",
    }
    assert {item["value"] for item in searched["facets"]["environment"]} == {
        "prod"
    }
    assert {item["value"] for item in searched["facets"]["risk"]} == {"clear"}
    with pytest.raises(ValueError, match="cursor is invalid"):
        build_inventory_page(rows, [], [], view="all", cursor="not-a-cursor")


def test_environment_owner_exposure_and_risk_are_server_filterable():
    exposed = _catalog(
        "azure:/subscriptions/s/accounts/a",
        source="azure_openai",
        provider="OpenAI",
        model_name="account-a",
        entity_type="ACCOUNT",
        environment="prod",
        owner="platform@example.com",
        key_auth_enabled=True,
    )
    clear = _catalog(
        "uc:dev.ml.churn",
        environment="dev",
        owner="ml@example.com",
    )
    page = build_inventory_page(
        [exposed, clear],
        [],
        [],
        view="all",
        environment="prod",
        owner="platform@example.com",
        exposure="key_auth",
        risk="attention",
    )

    assert [row["model_key"] for row in page["items"]] == [exposed["model_key"]]
    assert page["items"][0]["exposure"] == ["key_auth"]
    assert {item["value"] for item in page["facets"]["exposure"]} == {
        "key_auth",
        "none_attested",
    }


def test_source_health_exposes_partial_and_truncation():
    page = build_inventory_page(
        [_catalog("uc:prod.ml.churn")],
        [],
        [
            {
                "source_key": "ai-catalog-databricks-uc",
                "source": "UC registered models",
                "source_type": "inventory",
                "status": "partial",
                "row_count": 500,
                "notes": "listing capped at 500 models",
            },
            {
                "source_key": "azure-cost-management",
                "source": "Azure Cost",
                "source_type": "cost",
                "status": "available",
            },
        ],
    )

    assert page["source_health"] == [
        {
            "source_key": "ai-catalog-databricks-uc",
            "source": "UC registered models",
            "status": "partial",
            "row_count": 500,
            "freshness": None,
            "checked_at": None,
            "last_success_at": None,
            "notes": "listing capped at 500 models",
            "truncated": True,
        }
    ]


def test_detail_keeps_access_and_raw_entity_evidence():
    row = _catalog("uc:prod.ml.churn")
    access = [
        {
            "model_key": row["model_key"],
            "principal_name": "ml-team",
            "access_level": "INVOKE",
        }
    ]
    detail = build_inventory_detail(row["model_key"], [row], access, [])

    assert detail is not None
    assert detail["entity"]["display_name"] == "prod.ml.churn"
    assert detail["access"] == access
    assert build_inventory_detail("missing", [row], access, []) is None


def test_detail_inherits_access_from_serving_endpoint_and_azure_account():
    serving = _catalog(
        "serving:fraud-endpoint/prod.ml.fraud",
        source="databricks_serving",
        endpoint_name="fraud-endpoint",
    )
    azure = _catalog(
        "azure:/subscriptions/s/resourcegroups/rg/providers/"
        "microsoft.cognitiveservices/accounts/ai/deployments/gpt",
        source="azure_openai",
        resource_id="/subscriptions/s/resourcegroups/rg/providers/"
        "microsoft.cognitiveservices/accounts/ai/deployments/gpt",
    )
    access = [
        {
            "model_key": "serving:fraud-endpoint",
            "principal_name": "ml-team",
        },
        {
            "model_key": "azure:/subscriptions/s/resourcegroups/rg/providers/"
            "microsoft.cognitiveservices/accounts/ai",
            "principal_name": "account users",
        },
    ]

    serving_detail = build_inventory_detail(serving["model_key"], [serving], access, [])
    azure_detail = build_inventory_detail(azure["model_key"], [azure], access, [])

    assert serving_detail is not None
    assert serving_detail["access"][0]["principal_name"] == "ml-team"
    assert azure_detail is not None
    assert azure_detail["access"][0]["principal_name"] == "account users"
    assert azure_detail["entity"]["needs_attention"] is True

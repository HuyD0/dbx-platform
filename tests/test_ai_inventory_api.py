"""Offline route-contract tests for the model inventory explorer."""

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException

APP_DIR = Path(__file__).resolve().parent.parent / "apps" / "platform-console"
sys.path.insert(0, str(APP_DIR))

from backend.routers import ai_inventory as inventory_router  # noqa: E402


def _catalog(model_key: str) -> dict:
    return {
        "source": "databricks_uc",
        "model_key": model_key,
        "model_name": model_key.removeprefix("uc:"),
        "provider": "databricks",
        "entity_type": "REGISTERED_MODEL",
        "status": "READY",
        "owner": "owner@example.com",
        "key_auth_enabled": False,
    }


def test_inventory_route_returns_typed_page_and_source_health(monkeypatch):
    snapshots = (
        [
            _catalog("uc:system.ai.bge"),
            _catalog("uc:prod.ml.churn"),
        ],
        [],
        [
            {
                "source_key": "ai-catalog-databricks-uc",
                "source": "UC registered models",
                "source_type": "inventory",
                "status": "partial",
                "row_count": 2,
                "notes": "one grant unreadable",
            }
        ],
    )
    monkeypatch.setattr(
        inventory_router,
        "_snapshots",
        lambda refresh: (snapshots, datetime(2026, 7, 25, tzinfo=UTC), False),
    )

    response = inventory_router.inventory(
        q="",
        source=None,
        provider=None,
        status=None,
        entity_type=None,
        environment=None,
        owner=None,
        exposure=None,
        risk=None,
        view="managed_or_risky",
        cursor=None,
        limit=50,
        export=False,
        format="json",
        refresh=False,
    )

    assert response["count"] == 1
    assert [row["model_key"] for row in response["data"]["items"]] == [
        "uc:prod.ml.churn"
    ]
    assert response["data"]["summary"]["system"] == 1
    assert response["data"]["source_health"][0]["status"] == "partial"


def test_inventory_csv_exports_every_filtered_row(monkeypatch):
    snapshots = (
        [
            _catalog("uc:prod.ml.churn"),
            _catalog("uc:prod.ml.fraud"),
        ],
        [],
        [],
    )
    monkeypatch.setattr(
        inventory_router,
        "_snapshots",
        lambda refresh: (snapshots, datetime(2026, 7, 25, tzinfo=UTC), False),
    )

    response = inventory_router.inventory(
        q="",
        source=None,
        provider=None,
        status=None,
        entity_type=None,
        environment=None,
        owner=None,
        exposure=None,
        risk=None,
        view="all",
        cursor=None,
        limit=1,
        export=False,
        format="csv",
        refresh=False,
    )

    assert response.media_type == "text/csv"
    assert response.body.decode().count("\n") == 3
    assert "uc:prod.ml.churn" in response.body.decode()
    assert "uc:prod.ml.fraud" in response.body.decode()


def test_inventory_invalid_cursor_is_a_typed_client_error(monkeypatch):
    snapshots = ([_catalog("uc:prod.ml.churn")], [], [])
    monkeypatch.setattr(
        inventory_router,
        "_snapshots",
        lambda refresh: (snapshots, datetime(2026, 7, 25, tzinfo=UTC), False),
    )

    with pytest.raises(HTTPException) as error:
        inventory_router.inventory(
            q="",
            source=None,
            provider=None,
            status=None,
            entity_type=None,
            environment=None,
            owner=None,
            exposure=None,
            risk=None,
            view="all",
            cursor="not-a-cursor",
            limit=50,
            export=False,
            format="json",
            refresh=False,
        )

    assert error.value.status_code == 400
    assert error.value.detail == "cursor is invalid"


def test_inventory_detail_returns_access_and_404(monkeypatch):
    snapshots = (
        [_catalog("uc:prod.ml.churn")],
        [
            {
                "model_key": "uc:prod.ml.churn",
                "principal_name": "ml-team",
                "access_level": "INVOKE",
            }
        ],
        [],
    )
    monkeypatch.setattr(
        inventory_router,
        "_snapshots",
        lambda refresh: (snapshots, datetime(2026, 7, 25, tzinfo=UTC), True),
    )

    response = inventory_router.inventory_detail("uc:prod.ml.churn", refresh=False)
    assert response["cached"] is True
    assert response["data"]["access"][0]["principal_name"] == "ml-team"

    with pytest.raises(HTTPException) as error:
        inventory_router.inventory_detail("missing", refresh=False)
    assert error.value.status_code == 404


def test_snapshot_load_marks_access_read_failure_partial(monkeypatch):
    settings = type(
        "Settings",
        (),
        {"dashboard_catalog": "main", "dashboard_schema": "platform"},
    )()
    monkeypatch.setattr(inventory_router.deps, "get_settings", lambda: settings)
    monkeypatch.setattr(
        inventory_router.deps,
        "control_plane_scope",
        lambda: ("123", "prod"),
    )
    monkeypatch.setattr(inventory_router.deps, "get_ws", object)
    monkeypatch.setattr(inventory_router.deps, "warehouse_id", lambda: "wh")
    monkeypatch.setattr(
        inventory_router.ai_catalog,
        "read_catalog",
        lambda *_args, **_kwargs: [_catalog("uc:prod.ml.churn")],
    )
    monkeypatch.setattr(
        inventory_router.ai_catalog,
        "read_access",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError()),
    )
    monkeypatch.setattr(
        inventory_router.llm_cost,
        "read_llm_source_health",
        lambda *_args, **_kwargs: [],
    )

    catalog, access, health = inventory_router._load_snapshots()

    assert catalog[0]["model_key"] == "uc:prod.ml.churn"
    assert access == []
    assert health == [
        {
            "source_key": "ai-catalog-access",
            "source": "Model access evidence",
            "source_type": "inventory",
            "status": "partial",
            "row_count": 0,
            "notes": (
                "Catalog entities are available, but access evidence could not "
                "be read (PermissionError)."
            ),
        }
    ]

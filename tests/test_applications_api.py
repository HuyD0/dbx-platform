from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException

APP_DIR = Path(__file__).resolve().parent.parent / "apps" / "platform-console"
sys.path.insert(0, str(APP_DIR))

from backend.routers import applications  # noqa: E402

from dbx_platform.application_cost import resolve_evidence_rows  # noqa: E402


def _evidence():
    return resolve_evidence_rows(
        [
            {
                "workspace_id": "w1",
                "environment": "prod",
                "usage_date": "2026-07-25",
                "source": "databricks",
                "resource_type": "app",
                "resource_id": "app-1",
                "resource_name": "Learn App",
                "metadata_application": "Learn App",
                "service": "APPS_SERVERLESS",
                "workload": "APPS",
                "cost": 5,
                "currency": "USD",
                "pricing_basis": "DATABRICKS_LIST",
                "evidence_at": "2026-07-26T00:00:00Z",
                "scope": "workspace:w1",
            },
            {
                "workspace_id": "w1",
                "environment": "prod",
                "usage_date": "2026-07-25",
                "source": "azure",
                "resource_type": "azure_resource",
                "resource_id": "/subscriptions/sub/resourceGroups/shared/providers/x/a",
                "resource_group": "shared",
                "resource_name": "a",
                "tags": {"application": "Learn App"},
                "service": "",
                "workload": "",
                "cost": 20,
                "currency": "CAD",
                "pricing_basis": "AZURE_ACTUAL",
                "evidence_at": "2026-07-26T00:00:00Z",
                "scope": "subscription:sub;tag-query:subscription",
                "shared_scope": True,
            },
        ]
    )


def _stub_data(monkeypatch):
    monkeypatch.setattr(
        applications,
        "_application_data",
        lambda _window, _refresh: (
            _evidence(),
            [
                {
                    "source": "azure_cost_resources",
                    "status": "healthy",
                    "last_success_at": "2026-07-26T00:00:00Z",
                    "coverage_start": "2026-07-01",
                    "coverage_end": "2026-07-25",
                    "notes": "available",
                    "scope": "subscription:sub",
                    "freshness": "2026-07-26T00:00:00Z",
                },
                {
                    "source": "azure_cost_tags",
                    "status": "healthy",
                    "last_success_at": "2026-07-26T00:00:00Z",
                    "coverage_start": "2026-07-01",
                    "coverage_end": "2026-07-25",
                    "notes": "available",
                    "scope": "subscription:sub",
                    "freshness": "2026-07-26T00:00:00Z",
                },
                {
                    "source": "databricks_billing",
                    "status": "healthy",
                    "last_success_at": "2026-07-26T00:00:00Z",
                    "coverage_start": "2026-07-01",
                    "coverage_end": "2026-07-25",
                    "notes": "available",
                    "scope": "workspace:w1",
                    "freshness": "2026-07-26T00:00:00Z",
                }
            ],
            datetime(2026, 7, 26, tzinfo=UTC),
            False,
        ),
    )


def test_list_contract_keeps_ledgers_separate(monkeypatch):
    _stub_data(monkeypatch)
    response = applications.list_applications(
        window="30d",
        q=None,
        environment=None,
        source=None,
        cursor=None,
        limit=24,
        refresh=False,
    )
    summary = response["data"]["applications"][0]
    assert summary["application_key"] == "learn-app"
    assert {
        (ledger["pricing_basis"], ledger["currency"], ledger["amount"])
        for ledger in summary["ledgers"]
    } == {
        ("AZURE_ACTUAL", "CAD", 20.0),
        ("DATABRICKS_LIST", "USD", 5.0),
    }
    assert response["data"]["facets"] == {
        "environments": ["prod"],
        "sources": ["azure", "databricks"],
    }


def test_profile_and_evidence_contracts(monkeypatch):
    _stub_data(monkeypatch)
    profile = applications.application_profile(
        "Learn App",
        window="30d",
        refresh=False,
    )
    assert profile["data"]["application"]["application_key"] == "learn-app"
    assert profile["data"]["source_health"][0]["status"] == "healthy"
    assert profile["data"]["drivers"][0]["path"][0]["dimension"] == "source"

    evidence = applications.application_evidence(
        "learn-app",
        window="30d",
        environment=None,
        source=None,
        cursor=None,
        limit=50,
        format="json",
        refresh=False,
    )
    assert evidence["count"] == 2
    assert {
        row["attribution_method"] for row in evidence["data"]["items"]
    } == {"DIRECT_METADATA", "DIRECT_TAG"}
    assert all(row["scope"] for row in evidence["data"]["items"])
    assert all(row["evidence_refs"] for row in evidence["data"]["items"])


def test_evidence_csv_exports_full_filtered_result(monkeypatch):
    _stub_data(monkeypatch)
    response = applications.application_evidence(
        "learn-app",
        window="30d",
        environment=None,
        source="azure",
        cursor=None,
        limit=1,
        format="csv",
        refresh=False,
    )
    assert response.media_type == "text/csv"
    assert b"AZURE_ACTUAL" in response.body
    assert b"DATABRICKS_LIST" not in response.body
    assert b"evidence_refs" in response.body


def test_evidence_filters_and_invalid_cursor_are_typed(monkeypatch):
    _stub_data(monkeypatch)
    response = applications.application_evidence(
        "learn-app",
        window="30d",
        environment=None,
        source=None,
        q="app-1",
        attribution_method="DIRECT_METADATA",
        cursor=None,
        limit=50,
        format="json",
        refresh=False,
    )
    assert response["count"] == 1
    with pytest.raises(HTTPException) as exc:
        applications.list_applications(
            window="30d",
            q=None,
            environment=None,
            source=None,
            cursor="invalid",
            limit=24,
            refresh=False,
        )
    assert exc.value.status_code == 400

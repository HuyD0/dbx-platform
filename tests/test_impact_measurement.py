"""Post-window expected-versus-realized impact measurement tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

from dbx_platform.impact_measurement import measure_due_actions


def test_due_action_appends_one_coverage_aware_followup_event() -> None:
    calls: list[tuple[str, dict]] = []
    plan = {
        "targets": [{"resource_id": "101", "resource_key": "cost_usage_report"}],
        "impact": {
            "financial_impact_usd": 250,
            "risk_reduction": "resolve privileged grant drift",
        },
        "after_state": {"desired_state": "SLEEPING"},
    }
    immediate = {
        "follow_up": {
            "measure_after": "2026-07-18T12:00:00+00:00",
        }
    }

    def query(_w, sql, _warehouse, parameters=None, row_limit=5000):
        calls.append((sql, dict(parameters or {})))
        if "WITH immediate AS" in sql:
            return [
                {
                    "action_id": "action-1",
                    "action_type": "stale-clusters",
                    "plan_json": json.dumps(plan),
                    "plan_hash": "a" * 64,
                    "immediate_details_json": json.dumps(immediate),
                    "immediate_event_ts": "2026-07-17T12:00:00Z",
                }
            ]
        if "FROM `main`.`dbx_platform`.platform_findings" in sql:
            return [
                {
                    "finding_id": "finding-1",
                    "pillar": "SECURITY",
                    "state": "RESOLVED",
                    "affected_resources_json": json.dumps(
                        [{"resource_id": "101"}]
                    ),
                    "financial_impact_usd": 250,
                    "slo_impact": None,
                    "last_seen_at": "2026-07-17T13:00:00Z",
                }
            ]
        if "INSERT INTO" in sql:
            return []
        raise AssertionError(sql)

    result = measure_due_actions(
        MagicMock(),
        "warehouse-1",
        catalog="main",
        schema="dbx_platform",
        workspace_id="123",
        environment="prod",
        measured_at=datetime(2026, 7, 19, 12, tzinfo=UTC),
        query=query,
    )

    assert result[0]["resolved_findings"] == 1
    assert result[0]["financial_coverage"] == "UNATTRIBUTED"
    insert = next(parameters for sql, parameters in calls if "INSERT INTO" in sql)
    details = json.loads(insert["details_json"])
    assert details["observed"]["target_count"] == 1
    assert details["comparison"]["financial_savings"]["expected"] == 250
    assert details["comparison"]["financial_savings"]["realized"] is None
    assert details["comparison"]["risk_reduction"][
        "realized_resolved_findings"
    ] == 1


def test_no_due_action_performs_no_followup_reads_or_writes() -> None:
    calls = 0

    def query(_w, sql, _warehouse, parameters=None, row_limit=5000):
        nonlocal calls
        calls += 1
        assert "WITH immediate AS" in sql
        return []

    assert (
        measure_due_actions(
            MagicMock(),
            "warehouse-1",
            catalog="main",
            schema="dbx_platform",
            workspace_id="123",
            environment="prod",
            query=query,
        )
        == []
    )
    assert calls == 1


def test_common_target_identifiers_correlate_only_fresh_findings() -> None:
    calls: list[tuple[str, dict]] = []
    plan = {
        "targets": [
            {"cluster_id": "cluster-1"},
            {"job_id": 42},
            {"token_id": "token-9"},
            {"name": "restricted-policy"},
        ]
    }
    immediate = {
        "follow_up": {"measure_after": "2026-07-18T12:00:00+00:00"}
    }

    def query(_w, sql, _warehouse, parameters=None, row_limit=5000):
        calls.append((sql, dict(parameters or {})))
        if "WITH immediate AS" in sql:
            return [
                {
                    "action_id": "action-ids",
                    "action_type": "policy-drift",
                    "plan_json": json.dumps(plan),
                    "plan_hash": "b" * 64,
                    "immediate_details_json": json.dumps(immediate),
                    "immediate_event_ts": "2026-07-17T12:00:00Z",
                }
            ]
        if "platform_findings" in sql:
            return [
                {
                    "finding_id": "fresh",
                    "pillar": "SECURITY",
                    "state": "RESOLVED",
                    "affected_resources_json": json.dumps(
                        [{"job_id": 42}, {"name": "restricted-policy"}]
                    ),
                    "last_seen_at": "2026-07-18T13:00:00Z",
                },
                {
                    "finding_id": "stale",
                    "pillar": "SECURITY",
                    "state": "RESOLVED",
                    "affected_resources_json": json.dumps(
                        [{"cluster_id": "cluster-1"}]
                    ),
                    "last_seen_at": "2026-07-16T13:00:00Z",
                },
            ]
        if "INSERT INTO" in sql:
            return []
        raise AssertionError(sql)

    result = measure_due_actions(
        MagicMock(),
        "warehouse-1",
        catalog="main",
        schema="dbx_platform",
        workspace_id="123",
        environment="prod",
        measured_at=datetime(2026, 7, 19, 12, tzinfo=UTC),
        query=query,
    )

    assert result[0]["resolved_findings"] == 1
    inserted = next(parameters for sql, parameters in calls if "INSERT INTO" in sql)
    assert inserted["event_type"] == "IMPACT_FOLLOW_UP_MEASURED"


def test_missing_target_refresh_stays_retryable_during_grace_window() -> None:
    calls: list[tuple[str, dict]] = []
    plan = {"targets": [{"cluster_id": "cluster-1"}]}
    immediate = {
        "follow_up": {"measure_after": "2026-07-18T12:00:00+00:00"}
    }

    def query(_w, sql, _warehouse, parameters=None, row_limit=5000):
        calls.append((sql, dict(parameters or {})))
        if "WITH immediate AS" in sql:
            return [
                {
                    "action_id": "action-pending",
                    "action_type": "stale-clusters",
                    "plan_json": json.dumps(plan),
                    "plan_hash": "c" * 64,
                    "immediate_details_json": json.dumps(immediate),
                    "immediate_event_ts": "2026-07-17T12:00:00Z",
                }
            ]
        if "platform_findings" in sql:
            return []
        if "INSERT INTO" in sql:
            return []
        raise AssertionError(sql)

    result = measure_due_actions(
        MagicMock(),
        "warehouse-1",
        catalog="main",
        schema="dbx_platform",
        workspace_id="123",
        environment="prod",
        measured_at=datetime(2026, 7, 19, 12, tzinfo=UTC),
        query=query,
    )

    assert result[0]["status"] == "PENDING_SOURCE_CORRELATION"
    inserted = next(parameters for sql, parameters in calls if "INSERT INTO" in sql)
    assert inserted["event_type"] == "IMPACT_FOLLOW_UP_PENDING"

import json
from unittest.mock import MagicMock

from dbx_platform.digest import (
    build_digest_prompt,
    flatten_findings,
    store_digest,
    store_findings,
)

FINDINGS = {
    "cost/cluster-utilization": [
        {"cluster_id": "c-1", "cluster_name": "etl", "creator": "a@example.com",
         "list_cost_usd": 900.0, "reason": "p95 CPU 5%", "action": "downsize"}
    ],
    "security/token-audit": [
        {"token_id": "t-1", "owner": "b@example.com", "reason": "over age",
         "action": "revoke"}
    ],
    "ml/endpoint-audit": [],
}


def test_prompt_includes_counts_window_and_findings():
    prompt = build_digest_prompt(FINDINGS, {}, days=30)
    assert "last 30 days" in prompt
    assert "Total findings: 2" in prompt
    assert "cost/cluster-utilization" in prompt
    assert "b@example.com" in prompt


def test_prompt_omits_empty_checks_from_payload():
    prompt = build_digest_prompt(FINDINGS, {}, days=30)
    payload = prompt[prompt.index("{"):]
    assert "ml/endpoint-audit" not in payload


def test_prompt_is_deterministic():
    assert build_digest_prompt(FINDINGS, {}, 30) == build_digest_prompt(FINDINGS, {}, 30)


def test_prompt_names_skipped_checks():
    prompt = build_digest_prompt({}, {"cost/failed-run-waste": "no warehouse"}, 30)
    assert "could not run" in prompt
    assert "cost/failed-run-waste" in prompt


def test_prompt_without_skips_has_no_skip_note():
    assert "could not run" not in build_digest_prompt(FINDINGS, {}, 30)


def test_flatten_findings_rows_carry_area_check_and_resource():
    rows = flatten_findings(
        FINDINGS,
        workspace_id="workspace-1",
        environment="dev",
    )
    assert len(rows) == 2
    by_area = {r["area"]: r for r in rows}
    assert by_area["cost"]["check_name"] == "cluster-utilization"
    assert by_area["cost"]["resource"] == "etl"
    assert by_area["security"]["action"] == "revoke"
    assert by_area["security"]["pillar"] == "SECURITY"
    assert by_area["security"]["severity"] == "CRITICAL"
    assert by_area["cost"]["financial_impact_usd"] == 900
    assert by_area["cost"]["workspace_id"] == "workspace-1"
    assert by_area["cost"]["environment"] == "dev"
    assert len(by_area["cost"]["finding_id"]) == 64
    assert by_area["cost"]["confidence"] == 0.75
    assert by_area["cost"]["freshness_at"] is None
    assert json.loads(by_area["cost"]["affected_resources_json"])[0][
        "resource_id"
    ] == "c-1"
    # details round-trips as JSON
    assert json.loads(by_area["cost"]["details"])["list_cost_usd"] == 900.0


def test_flatten_findings_preserves_only_timestamp_shaped_source_freshness():
    findings = {
        "performance/query-regression": [
            {
                "full_name": "query:abc",
                "reason": "p95 regressed",
                "freshness": "2026-07-17T12:30:00Z",
            },
            {
                "full_name": "query:def",
                "reason": "p95 regressed",
                "freshness": "query-time metadata snapshot",
            },
        ]
    }

    rows = flatten_findings(findings)

    assert rows[0]["freshness_at"] == "2026-07-17T12:30:00Z"
    assert rows[1]["freshness_at"] is None


def test_flatten_findings_empty():
    assert flatten_findings({}) == []


def test_store_findings_accepts_explicit_governed_scope(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "dbx_platform.digest.run_query",
        lambda _w, sql, _warehouse, parameters=None: calls.append(
            (sql, parameters)
        )
        or [],
    )
    workspace = MagicMock()
    workspace.get_workspace_id.return_value = 999

    stored = store_findings(
        workspace,
        "warehouse",
        "main",
        "dbx_platform",
        {"performance/job-duration-regression": []},
        workspace_id="123",
        environment="dev",
    )

    assert stored == 0
    assert len(calls) == 1
    assert calls[0][1]["workspace_id"] == "123"
    assert calls[0][1]["environment"] == "dev"
    workspace.get_workspace_id.assert_not_called()


def test_store_digest_forwards_exact_workspace_environment_scope(monkeypatch):
    stored = MagicMock()
    monkeypatch.setattr("dbx_platform.digest.run_query", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("dbx_platform.digest.store_findings", stored)

    store_digest(
        MagicMock(),
        "warehouse",
        "main",
        "dbx_platform",
        30,
        "model",
        "summary",
        FINDINGS,
        workspace_id="workspace-1",
        environment="dev",
    )

    assert stored.call_args.kwargs == {
        "workspace_id": "workspace-1",
        "environment": "dev",
    }


def test_ai_areas_map_to_pillars_and_keep_explicit_severity():
    rows = flatten_findings(
        {
            "ai-catalog/azure-key-auth": [
                {"name": "foundry-prod", "resource_id": "/subs/x/acct",
                 "resource_type": "AI_ACCOUNT",
                 "reason": "key auth enabled",
                 "action": "disable-key-auth (manual)", "severity": "HIGH"}
            ],
            "ai-monitor/idle-endpoint": [
                {"name": "old-api", "reason": "no requests in 30d",
                 "resource_type": "SERVING_ENDPOINT",
                 "action": "review-or-delete (manual)", "severity": "LOW"}
            ],
        }
    )
    by_area = {row["area"]: row for row in rows}
    assert by_area["ai-catalog"]["pillar"] == "SECURITY"
    assert by_area["ai-catalog"]["severity"] == "HIGH"
    assert by_area["ai-monitor"]["pillar"] == "PERFORMANCE"
    assert by_area["ai-monitor"]["severity"] == "LOW"
    affected = json.loads(by_area["ai-monitor"]["affected_resources_json"])
    assert affected[0]["resource_type"] == "SERVING_ENDPOINT"

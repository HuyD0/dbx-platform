import json
from pathlib import Path

import yaml

from dbx_platform import operational
from dbx_platform.digest import flatten_findings


def test_job_duration_regression_is_deterministic_and_explicit():
    rows = [
        {
            "job_id": "42",
            "job_name": "daily-etl",
            "recent_samples": "8",
            "baseline_samples": "20",
            "recent_p95_duration_seconds": "180",
            "baseline_p95_duration_seconds": "100",
            "evidence_freshness_at": "2026-07-18T10:00:00Z",
        }
    ]

    findings = operational.classify_job_duration(rows)

    assert len(findings) == 1
    finding = findings[0]
    assert finding["source"] == "system.lakeflow.job_run_timeline"
    assert finding["check"] == "job-duration-regression"
    assert finding["freshness"] == "2026-07-18T10:00:00Z"
    assert finding["confidence"] == 0.90
    assert finding["blast_radius"] == "ONE_JOB"
    assert finding["report_only"] is True
    assert finding["regression_ratio"] == 1.8
    assert "UNSUPPORTED" in finding["financial_impact_coverage"]

    canonical = flatten_findings(
        {operational.JOB_DURATION_CHECK: findings},
        workspace_id="123",
        environment="prod",
    )[0]
    assert canonical["check_name"] == "job-duration-regression"
    assert canonical["confidence"] == 0.90
    assert canonical["blast_radius"] == "ONE_JOB"
    evidence = json.loads(canonical["evidence_json"])
    assert evidence["source"] == "system.lakeflow.job_run_timeline"
    assert evidence["freshness"] == "2026-07-18T10:00:00Z"


def test_job_waste_splits_failure_retry_and_queue_signals():
    rows = [
        {
            "job_id": "42",
            "job_name": "daily-etl",
            "attempts": "10",
            "failed_attempts": "3",
            "retry_attempts": "4",
            "queue_metric_attempts": "8",
            "p95_queue_seconds": "420",
            "total_queue_seconds": "3600",
            "evidence_freshness_at": "2026-07-18T10:00:00Z",
        }
    ]

    findings = operational.classify_job_waste(rows)

    assert set(findings) == {
        operational.JOB_FAILURE_CHECK,
        operational.JOB_RETRY_CHECK,
        operational.JOB_QUEUE_CHECK,
    }
    assert findings[operational.JOB_FAILURE_CHECK][0]["failure_rate"] == 0.3
    assert findings[operational.JOB_RETRY_CHECK][0]["retry_attempts"] == 4
    assert findings[operational.JOB_QUEUE_CHECK][0]["p95_queue_seconds"] == 420


def test_job_queue_check_is_unsupported_when_metric_is_not_populated():
    findings = operational.classify_job_waste(
        [
            {
                "job_id": "42",
                "attempts": "10",
                "failed_attempts": "0",
                "retry_attempts": "0",
                "queue_metric_attempts": "0",
            }
        ]
    )

    assert operational.JOB_QUEUE_CHECK not in findings


def test_query_regressions_never_include_query_text():
    rows = [
        {
            "query_fingerprint": "a" * 64,
            "compute_id": "warehouse-1",
            "statement_type": "SELECT",
            "recent_samples": "10",
            "baseline_samples": "12",
            "recent_p95_duration_ms": "15000",
            "baseline_p95_duration_ms": "8000",
            "recent_p95_queue_ms": "7000",
            "baseline_p95_queue_ms": "2000",
            "evidence_freshness_at": "2026-07-18T10:00:00Z",
        }
    ]

    findings = operational.classify_query_regressions(rows)

    assert len(findings[operational.QUERY_DURATION_CHECK]) == 1
    assert len(findings[operational.QUERY_QUEUE_CHECK]) == 1
    duration = findings[operational.QUERY_DURATION_CHECK][0]
    assert duration["full_name"] == f"query:{'a' * 64}"
    assert duration["resource_type"] == "QUERY"
    assert "query_text" not in duration
    assert "UNSUPPORTED" in duration["query_text_coverage"]


def test_inefficient_scan_is_a_conservative_partial_signal():
    findings = operational.classify_query_scans(
        [
            {
                "query_fingerprint": "b" * 64,
                "compute_id": "warehouse-1",
                "executions": "4",
                "total_read_bytes": str(12 * 1024**3),
                "total_output_rows": "100",
                "evidence_freshness_at": "2026-07-18T10:00:00Z",
            }
        ]
    )

    assert len(findings) == 1
    assert findings[0]["bytes_per_output_row"] > 1024**2
    assert "not a query-plan diagnosis" in findings[0]["coverage"]
    assert "UNSUPPORTED" in findings[0]["query_plan_coverage"]


def test_serving_findings_use_only_populated_canonical_metrics():
    rows = [
        {
            "endpoint": "chat-prod",
            "provider": "anthropic",
            "model": "claude",
            "source": "system.ai_gateway.usage",
            "recent_requests": "100",
            "baseline_requests": "100",
            "recent_weighted_p95_latency_ms": "8000",
            "baseline_weighted_p95_latency_ms": "4000",
            "recent_latency_metric_rows": "12",
            "recent_errors": "8",
            "recent_error_metric_rows": "12",
            "evidence_freshness_at": "2026-07-18T10:00:00Z",
        }
    ]

    findings = operational.classify_serving(rows)

    assert len(findings[operational.SERVING_LATENCY_CHECK]) == 1
    assert len(findings[operational.SERVING_ERROR_CHECK]) == 1
    assert findings[operational.SERVING_ERROR_CHECK][0]["error_rate"] == 0.08
    assert (
        "weighted hourly p95"
        in (findings[operational.SERVING_LATENCY_CHECK][0]["percentile_coverage"])
    )

    legacy = operational.classify_serving(
        [
            {
                "endpoint": "legacy",
                "recent_requests": "100",
                "recent_latency_metric_rows": "0",
                "recent_error_metric_rows": "0",
            }
        ]
    )
    assert operational.SERVING_LATENCY_CHECK not in legacy
    assert operational.SERVING_ERROR_CHECK not in legacy


def test_uc_security_signals_are_narrow_and_report_only():
    grant = operational.classify_uc_grants(
        [
            {
                "table_catalog": "main",
                "table_schema": "finance",
                "table_name": "actuals",
                "grantee": "account users",
                "privilege_type": "MODIFY",
                "inherited_from": "main.finance",
                "table_owner": "finance-admins",
            }
        ]
    )[0]
    owner = operational.classify_uc_missing_owners(
        [
            {
                "table_catalog": "main",
                "table_schema": "finance",
                "table_name": "orphaned",
                "creator_principal": "creator@example.com",
            }
        ]
    )[0]

    assert grant["owner"] == "finance-admins"
    assert grant["blast_radius"] == "ONE_TABLE_ALL_ACCOUNT_USERS"
    assert grant["report_only"] is True
    assert "SELECT or BROWSE" in grant["read_only_grants_coverage"]
    assert owner["confidence"] == 1.0
    assert "TABLE_OWNER non-null" in owner["coverage"]


def test_pat_and_inactive_user_resources_are_pseudonymous():
    pat = operational.normalize_pat_findings(
        [
            {
                "token_id": "token-secret-id",
                "created_by": "owner@example.com",
                "comment": "must not persist",
                "age_days": 120,
                "issues": "never expires; age 120d > 90d",
                "over_age": True,
            }
        ]
    )[0]
    inactive = operational.normalize_inactive_user_findings(
        [
            {
                "user_name": "inactive@example.com",
                "display_name": "Inactive User",
                "reason": "no audited activity in 90d",
            }
        ]
    )[0]

    assert pat["resource_id"].startswith("pat-")
    assert "token-secret-id" not in pat["resource_id"]
    assert pat["token_id"] == "token-secret-id"
    assert "comment" not in pat
    assert pat["report_only"] is True
    assert inactive["resource_id"].startswith("user-")
    assert "inactive@example.com" not in inactive["resource_id"]
    assert inactive["user_name"] == "inactive@example.com"
    assert "UNSUPPORTED" in inactive["deactivation_coverage"]


def test_unavailable_source_is_omitted_so_prior_findings_are_preserved():
    def query(name, **_kwargs):
        if name == "job_duration_rows":
            raise RuntimeError("not granted")
        return []

    findings, coverage = operational.collect_findings(
        object(),
        "warehouse",
        catalog="main",
        schema="dbx_platform",
        workspace_id="123",
        environment="prod",
        query=query,
    )

    assert operational.JOB_DURATION_CHECK not in findings
    assert operational.JOB_FAILURE_CHECK in findings
    duration_coverage = next(row for row in coverage if row["check"] == "job-duration-regression")
    assert duration_coverage["status"] == "UNAVAILABLE"
    assert "preserved" in duration_coverage["notes"]


def test_successful_empty_privileged_sources_refresh_empty_checks(
    monkeypatch,
):
    monkeypatch.setattr(operational.security, "fetch_tokens", lambda _w: [])
    monkeypatch.setattr(
        operational.security,
        "fetch_workspace_users",
        lambda _w: [],
    )

    findings, _coverage = operational.collect_findings(
        object(),
        "warehouse",
        catalog="main",
        schema="dbx_platform",
        workspace_id="123",
        environment="prod",
        query=lambda _name, **_kwargs: [],
    )

    assert findings[operational.PAT_TOKEN_CHECK] == []
    assert findings[operational.INACTIVE_USER_CHECK] == []


def test_stale_serving_ledger_is_not_used_to_resolve_findings():
    def query(name, **_kwargs):
        if name == "serving_rows":
            return [
                {
                    "endpoint": "stale",
                    "recent_requests": "100",
                    "recent_latency_metric_rows": "1",
                    "recent_error_metric_rows": "1",
                    "freshness_age_hours": "72",
                    "evidence_freshness_at": "2026-07-15T10:00:00Z",
                }
            ]
        return []

    findings, coverage = operational.collect_findings(
        object(),
        "warehouse",
        catalog="main",
        schema="dbx_platform",
        workspace_id="123",
        environment="prod",
        query=query,
    )

    assert operational.SERVING_LATENCY_CHECK not in findings
    assert operational.SERVING_ERROR_CHECK not in findings
    serving = [row for row in coverage if row["check"] in {"serving-latency", "serving-error-rate"}]
    assert {row["status"] for row in serving} == {"UNAVAILABLE"}


def test_sql_sources_are_scoped_and_query_text_is_only_hashed():
    root = Path(__file__).resolve().parent.parent
    queries = root / "src" / "dbx_platform" / "queries"
    job_sql = (queries / "operational_job_waste.sql").read_text()
    query_sql = (queries / "operational_query_regression.sql").read_text()
    grant_sql = (queries / "operational_uc_privileged_grants.sql").read_text()

    assert "workspace_id = :workspace_id" in job_sql
    assert "per_run_retries AS" in job_sql
    assert "GROUP BY workspace_id, job_id, run_id" in job_sql
    assert "workspace_id = :workspace_id" in query_sql
    assert "SHA2(" in query_sql
    assert "statement_text" not in query_sql.split("SELECT", 2)[-1]
    assert "p.table_catalog = :catalog" in grant_sql
    assert "p.grantee = 'account users'" in grant_sql
    assert "('ALL PRIVILEGES', 'MANAGE', 'MODIFY')" in grant_sql


def test_operational_task_reuses_existing_governed_schedule():
    root = Path(__file__).resolve().parent.parent
    resource = yaml.safe_load((root / "resources" / "report_jobs.yml").read_text())
    job = resource["resources"]["jobs"]["platform_digest"]
    tasks = {task["task_key"]: task for task in job["tasks"]}

    assert "schedule" in job
    assert set(tasks) == {
        "operational_findings",
        "impact_followup",
        "ai_digest",
    }
    assert tasks["impact_followup"]["depends_on"] == [
        {"task_key": "operational_findings"}
    ]
    assert tasks["ai_digest"]["depends_on"] == [{"task_key": "impact_followup"}]
    parameters = tasks["operational_findings"]["python_wheel_task"]["parameters"]
    assert parameters[:2] == ["report", "operational-findings"]
    assert {
        "--approved-action-id",
        "--approved-plan-hash",
        "--approved-job-id",
        "--approved-run-id",
        "--trigger-type",
        "--environment",
    }.issubset(parameters)
    assert "{{job.id}}" in parameters
    assert "{{job.run_id}}" in parameters
    assert "{{job.trigger.type}}" in parameters


def test_impact_followup_also_runs_on_existing_daily_ledger_schedule():
    root = Path(__file__).resolve().parent.parent
    resource = yaml.safe_load((root / "resources" / "llm_cost_jobs.yml").read_text())
    job = resource["resources"]["jobs"]["llm_cost_rollup"]
    tasks = {task["task_key"]: task for task in job["tasks"]}

    assert set(tasks) == {"rollup", "impact_followup"}
    assert tasks["impact_followup"]["depends_on"] == [{"task_key": "rollup"}]
    parameters = tasks["impact_followup"]["python_wheel_task"]["parameters"]
    assert parameters[:2] == ["report", "impact-followup"]
    assert "{{job.trigger.type}}" in parameters

"""Static contracts for the read-only AppKit analytics query scaffold."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUERY_DIR = ROOT / "apps" / "platform-console" / "config" / "queries"

EXPECTED_QUERIES = {
    "ai_gateway_agent_workflows.obo.sql",
    "ai_gateway_latency.obo.sql",
    "ai_gateway_tag_attribution.obo.sql",
    "ai_gateway_throttling.obo.sql",
    "ai_gateway_token_throughput.obo.sql",
    "data_quality_alerts.obo.sql",
}
MUTATING_KEYWORDS = {
    "ALTER",
    "CALL",
    "CREATE",
    "DELETE",
    "DROP",
    "INSERT",
    "MERGE",
    "OPTIMIZE",
    "UPDATE",
    "VACUUM",
}


def _query_files() -> list[Path]:
    return sorted(QUERY_DIR.glob("*.sql"))


def _without_comments(sql: str) -> str:
    return "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )


def test_query_scaffold_is_flat_and_obo_only():
    files = _query_files()
    assert {path.name for path in files} == EXPECTED_QUERIES
    assert all(path.parent == QUERY_DIR for path in files)
    assert all(path.name.endswith(".obo.sql") for path in files)


def test_queries_are_single_read_only_statements():
    for path in _query_files():
        body = _without_comments(path.read_text())
        first_keyword = re.search(r"\b[A-Za-z]+\b", body)
        assert first_keyword and first_keyword.group(0).upper() in {"SELECT", "WITH"}
        assert ";" not in body, f"{path.name} must contain one statement"
        keywords = {token.upper() for token in re.findall(r"\b[A-Za-z]+\b", body)}
        assert not keywords.intersection(MUTATING_KEYWORDS), (
            f"{path.name} contains a mutating SQL keyword"
        )
        assert "SELECT *" not in body.upper()


def test_all_runtime_parameters_are_typed_except_injected_workspace_id():
    for path in _query_files():
        sql = path.read_text()
        used = set(re.findall(r":([A-Za-z][A-Za-z0-9_]*)", sql))
        annotated = set(
            re.findall(r"^-- @param ([A-Za-z][A-Za-z0-9_]*) ", sql, re.MULTILINE)
        )
        assert "workspaceId" not in annotated
        assert used - {"workspaceId"} == annotated


def test_ai_gateway_queries_are_workspace_and_time_scoped():
    for path in _query_files():
        if not path.name.startswith("ai_gateway_"):
            continue
        sql = path.read_text()
        assert "FROM system.ai_gateway.usage" in sql
        assert "workspace_id = :workspaceId" in sql
        assert "DATE_SUB(CURRENT_DATE(), :lookbackDays)" in sql


def test_gateway_contract_covers_required_observability_dimensions():
    latency = (QUERY_DIR / "ai_gateway_latency.obo.sql").read_text()
    assert {"p50_latency_ms", "p95_latency_ms", "p99_latency_ms"} <= set(
        re.findall(r"\b[pP]\d+_[a-z_]+\b", latency)
    )

    throttling = (QUERY_DIR / "ai_gateway_throttling.obo.sql").read_text()
    assert "status_code = 429" in throttling

    workflows = (QUERY_DIR / "ai_gateway_agent_workflows.obo.sql").read_text()
    assert "COUNT(DISTINCT invocation_id) > 1" in workflows
    assert "request_tags['team']" in workflows
    assert "request_tags['agent']" in workflows

    attribution = (QUERY_DIR / "ai_gateway_tag_attribution.obo.sql").read_text()
    assert "EXPLODE(request_tags)" in attribution


def test_data_quality_query_uses_documented_nested_checks():
    sql = (QUERY_DIR / "data_quality_alerts.obo.sql").read_text()
    assert "FROM system.data_quality_monitoring.table_results" in sql
    assert "catalog_name = :catalogName" in sql
    assert "schema_name = :schemaName" in sql
    assert "freshness.commit_freshness.predicted_value" in sql
    assert "completeness.daily_row_count.min_predicted_value" in sql
    assert "PARTITION BY table_id" in sql

"""Coverage-aware follow-up measurement for completed Mission Control actions.

Executors record immediate target verification. This collector runs after the
observation window, correlates exact targets with current runtime state and
canonical findings, and appends one follow-up event. Unsupported financial or
SLO attribution stays explicitly unavailable instead of being estimated.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from databricks.sdk import WorkspaceClient

from dbx_platform.resource_identifiers import (
    extract_resource_ids,
    parse_resource_ids,
)
from dbx_platform.system_tables import run_query

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESOLVED_STATES = {"RESOLVED", "CLOSED", "REMEDIATED"}
_SOURCE_CORRELATION_GRACE = timedelta(days=7)

Query = Callable[..., list[dict[str, Any]]]


def _prefix(catalog: str, schema: str) -> str:
    if not _IDENTIFIER.fullmatch(catalog) or not _IDENTIFIER.fullmatch(schema):
        raise ValueError("Unsafe impact-measurement catalog or schema.")
    return f"`{catalog}`.`{schema}`"


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Malformed {label} JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be a JSON object.")
    return parsed


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _due_actions_sql(prefix: str) -> str:
    return f"""
    WITH immediate AS (
      SELECT workspace_id, environment, action_id, details_json, event_ts,
             ROW_NUMBER() OVER (
               PARTITION BY workspace_id, environment, action_id
               ORDER BY event_ts DESC
             ) AS row_number
      FROM {prefix}.action_events
      WHERE event_type = 'IMPACT_MEASUREMENT'
    )
    SELECT request.action_id, request.action_type, request.plan_json,
           request.plan_hash, immediate.details_json AS immediate_details_json,
           immediate.event_ts AS immediate_event_ts
    FROM {prefix}.action_requests AS request
    INNER JOIN immediate
      ON immediate.workspace_id = request.workspace_id
     AND immediate.environment = request.environment
     AND immediate.action_id = request.action_id
     AND immediate.row_number = 1
    WHERE request.workspace_id = :workspace_id
      AND request.environment = :environment
      AND request.status = 'SUCCEEDED'
      AND TRY_CAST(
            get_json_object(immediate.details_json, '$.follow_up.measure_after')
            AS TIMESTAMP
          ) <= CAST(:measured_at AS TIMESTAMP)
      AND NOT EXISTS (
        SELECT 1
        FROM {prefix}.action_events AS measured
        WHERE measured.workspace_id = request.workspace_id
          AND measured.environment = request.environment
          AND measured.action_id = request.action_id
          AND measured.event_type = 'IMPACT_FOLLOW_UP_MEASURED'
      )
    ORDER BY immediate.event_ts
    LIMIT :row_limit
    """


def _finding_rows(
    w: WorkspaceClient,
    warehouse_id: str,
    prefix: str,
    workspace_id: str,
    environment: str,
    *,
    query: Query,
) -> list[dict[str, Any]]:
    return query(
        w,
        f"""
        SELECT finding_id, pillar, state, affected_resources_json,
               financial_impact_usd, slo_impact, last_seen_at
        FROM {prefix}.platform_findings
        WHERE workspace_id = :workspace_id
          AND environment = :environment
        ORDER BY last_seen_at DESC
        LIMIT 5000
        """,
        warehouse_id,
        parameters={
            "workspace_id": workspace_id,
            "environment": environment,
        },
        row_limit=5000,
    )


def _runtime_row(
    w: WorkspaceClient,
    warehouse_id: str,
    prefix: str,
    workspace_id: str,
    environment: str,
    *,
    query: Query,
) -> dict[str, Any] | None:
    rows = query(
        w,
        f"""
        SELECT desired_state, actual_state, active_action_id,
               last_reconciled_at, updated_at
        FROM {prefix}.platform_runtime_state
        WHERE workspace_id = :workspace_id
          AND environment = :environment
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        warehouse_id,
        parameters={
            "workspace_id": workspace_id,
            "environment": environment,
        },
        row_limit=1,
    )
    return dict(rows[0]) if rows else None


def _measurement(
    row: Mapping[str, Any],
    findings: list[dict[str, Any]],
    runtime: dict[str, Any] | None,
    measured_at: datetime,
) -> dict[str, Any]:
    plan = _json_object(row.get("plan_json"), "action plan")
    immediate = _json_object(
        row.get("immediate_details_json"),
        "immediate impact measurement",
    )
    targets = list(plan.get("targets") or [])
    target_ids = extract_resource_ids(targets)
    immediate_event_at = _timestamp(row.get("immediate_event_ts"))
    matched = [
        finding
        for finding in findings
        if target_ids.intersection(
            parse_resource_ids(finding.get("affected_resources_json"))
        )
        and (
            immediate_event_at is None
            or (_timestamp(finding.get("last_seen_at")) or datetime.min.replace(tzinfo=UTC))
            >= immediate_event_at
        )
    ]
    resolved = [
        finding
        for finding in matched
        if str(finding.get("state") or "").upper() in _RESOLVED_STATES
    ]
    open_findings = [finding for finding in matched if finding not in resolved]
    action_type = str(row.get("action_type") or "")
    runtime_observation = runtime if action_type.startswith("runtime.") else None
    runtime_expected = (
        str(plan.get("after_state", {}).get("desired_state") or "")
        if action_type.startswith("runtime.")
        else ""
    )
    runtime_matches = (
        runtime_observation is not None
        and str(runtime_observation.get("desired_state") or "") == runtime_expected
        and str(runtime_observation.get("actual_state") or "") == runtime_expected
    )
    measure_after = _timestamp(immediate.get("follow_up", {}).get("measure_after"))
    awaiting_source_correlation = (
        bool(target_ids)
        and not matched
        and runtime_observation is None
        and measure_after is not None
        and measured_at < measure_after + _SOURCE_CORRELATION_GRACE
    )
    expected = dict(plan.get("impact") or immediate.get("expected") or {})
    performance_resolved = len(
        [
            finding
            for finding in resolved
            if str(finding.get("pillar") or "").upper() == "PERFORMANCE"
        ]
    )
    return {
        "status": (
            "PENDING_SOURCE_CORRELATION"
            if awaiting_source_correlation
            else "MEASURED_WITH_AVAILABLE_COVERAGE"
        ),
        "measured_at": measured_at.astimezone(UTC).isoformat(),
        "observation_window": {
            "immediate_event_at": str(row.get("immediate_event_ts") or ""),
            "measure_after": immediate.get("follow_up", {}).get("measure_after"),
        },
        "expected": expected,
        "observed": {
            "target_count": len(targets),
            "matched_finding_count": len(matched),
            "resolved_finding_count": len(resolved),
            "open_finding_count": len(open_findings),
            "runtime": runtime_observation,
            "runtime_matches_expected_state": runtime_matches
            if runtime_observation is not None
            else None,
        },
        "comparison": {
            "financial_savings": {
                "expected": expected.get("financial_impact_usd")
                or expected.get("estimated_savings"),
                "realized": None,
                "coverage": "UNATTRIBUTED",
                "reason": (
                    "Billing data has no exact action-to-charge attribution; "
                    "no savings value was invented."
                ),
            },
            "risk_reduction": {
                "expected": expected.get("risk_reduction")
                or expected.get("summary"),
                "realized_resolved_findings": len(resolved),
                "remaining_open_findings": len(open_findings),
                "coverage": "TARGET_MATCHED_FINDINGS"
                if matched
                else "NO_MATCHED_FINDINGS",
            },
            "performance_change": {
                "expected": expected.get("slo_impact")
                or expected.get("performance_change"),
                "realized_resolved_findings": performance_resolved,
                "coverage": "TARGET_MATCHED_FINDINGS"
                if matched
                else "NO_MATCHED_FINDINGS",
            },
        },
        "source_coverage": {
            "canonical_findings": (
                "AWAITING_TARGET_REFRESH"
                if awaiting_source_correlation
                else "AVAILABLE"
            ),
            "runtime_state": "AVAILABLE"
            if runtime_observation is not None
            else "NOT_APPLICABLE",
            "financial_attribution": "UNAVAILABLE",
        },
    }


def measure_due_actions(
    w: WorkspaceClient,
    warehouse_id: str,
    *,
    catalog: str,
    schema: str,
    workspace_id: str,
    environment: str,
    measured_at: datetime | None = None,
    limit: int = 100,
    query: Query = run_query,
) -> list[dict[str, Any]]:
    """Append exactly one post-window outcome event for each due action."""

    if not workspace_id or not environment:
        raise ValueError("Workspace and environment scope are required.")
    measured_at = (measured_at or datetime.now(UTC)).astimezone(UTC)
    limit = max(1, min(500, int(limit)))
    prefix = _prefix(catalog, schema)
    due = query(
        w,
        _due_actions_sql(prefix),
        warehouse_id,
        parameters={
            "workspace_id": workspace_id,
            "environment": environment,
            "measured_at": measured_at.isoformat(),
            "row_limit": limit,
        },
        row_limit=limit,
    )
    if not due:
        return []

    findings = _finding_rows(
        w,
        warehouse_id,
        prefix,
        workspace_id,
        environment,
        query=query,
    )
    needs_runtime = any(
        str(row.get("action_type") or "").startswith("runtime.") for row in due
    )
    runtime = (
        _runtime_row(
            w,
            warehouse_id,
            prefix,
            workspace_id,
            environment,
            query=query,
        )
        if needs_runtime
        else None
    )
    output: list[dict[str, Any]] = []
    for row in due:
        details = _measurement(row, findings, runtime, measured_at)
        event_id = str(uuid.uuid4())
        event_type = (
            "IMPACT_FOLLOW_UP_PENDING"
            if details["status"] == "PENDING_SOURCE_CORRELATION"
            else "IMPACT_FOLLOW_UP_MEASURED"
        )
        query(
            w,
            f"""
            INSERT INTO {prefix}.action_events (
              workspace_id, environment, event_id, action_id, event_type,
              from_status, to_status, actor_id, details_json, event_ts
            ) VALUES (
              :workspace_id, :environment, :event_id, :action_id,
              :event_type, 'SUCCEEDED', 'SUCCEEDED',
              'impact-measurement-job', :details_json, :event_ts
            )
            """,
            warehouse_id,
            parameters={
                "workspace_id": workspace_id,
                "environment": environment,
                "event_id": event_id,
                "action_id": str(row["action_id"]),
                "event_type": event_type,
                "details_json": json.dumps(
                    details,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ),
                "event_ts": measured_at.isoformat(),
            },
            row_limit=1,
        )
        output.append(
            {
                "action_id": str(row["action_id"]),
                "action_type": str(row.get("action_type") or ""),
                "event_id": event_id,
                "status": details["status"],
                "resolved_findings": details["observed"][
                    "resolved_finding_count"
                ],
                "financial_coverage": details["comparison"][
                    "financial_savings"
                ]["coverage"],
            }
        )
    return output

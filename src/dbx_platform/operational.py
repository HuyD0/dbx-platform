"""Bounded, report-only operational findings from trusted telemetry.

The v1 pack deliberately uses aggregated system-table fields and the canonical
``llm_usage_hourly`` ledger.  It does not inspect query text, reconstruct
request-level percentiles, estimate dollar impact, or mutate any monitored
resource.  Each source is isolated: an unavailable or unsupported check is
omitted from the findings map so ``digest.store_findings`` cannot resolve
previous evidence using an incomplete refresh.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from typing import Any

from databricks.sdk import WorkspaceClient

from dbx_platform import security
from dbx_platform.system_tables import load_query, run_query

DEFAULT_RECENT_DAYS = 7
DEFAULT_BASELINE_DAYS = 28
DEFAULT_LIMIT = 100

MIN_REGRESSION_SAMPLES = 5
REGRESSION_RATIO = 1.5
MIN_JOB_P95_SECONDS = 60.0
MIN_QUERY_P95_MS = 5_000.0
MIN_FAILED_ATTEMPTS = 3
MIN_RETRY_ATTEMPTS = 3
MIN_FAILURE_RATE = 0.20
MIN_JOB_QUEUE_P95_SECONDS = 300.0
MIN_JOB_QUEUE_TOTAL_SECONDS = 1_800.0
MIN_QUERY_QUEUE_P95_MS = 5_000.0
MIN_SCAN_EXECUTIONS = 3
MIN_SCAN_BYTES = 10 * 1024**3
MIN_SCAN_BYTES_PER_OUTPUT_ROW = 1024**2
MIN_SERVING_REQUESTS = 20
MIN_SERVING_LATENCY_MS = 5_000.0
MIN_SERVING_ERROR_RATE = 0.05
MAX_LEDGER_FRESHNESS_HOURS = 48.0

JOB_DURATION_CHECK = "performance/job-duration-regression"
JOB_FAILURE_CHECK = "performance/job-failure-waste"
JOB_RETRY_CHECK = "performance/job-retry-waste"
JOB_QUEUE_CHECK = "performance/job-queue-waste"
QUERY_DURATION_CHECK = "performance/query-duration-regression"
QUERY_QUEUE_CHECK = "performance/query-queue-regression"
QUERY_SCAN_CHECK = "performance/query-inefficient-scan"
SERVING_LATENCY_CHECK = "performance/serving-latency"
SERVING_ERROR_CHECK = "performance/serving-error-rate"
UC_GRANT_CHECK = "security/uc-broad-privileged-grant"
UC_OWNER_CHECK = "security/uc-missing-owner"
PAT_TOKEN_CHECK = "security/pat-token-hygiene"
INACTIVE_USER_CHECK = "security/inactive-user"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any) -> int:
    return int(_number(value))


def _check_name(key: str) -> str:
    return key.partition("/")[2]


def _stable_resource(kind: str, value: str) -> str:
    digest = hashlib.sha256(value.strip().lower().encode()).hexdigest()[:16]
    return f"{kind}-{digest}"


def _source_freshness(rows: list[dict]) -> str:
    values = [
        str(row.get("evidence_freshness_at") or "")
        for row in rows
        if row.get("evidence_freshness_at")
    ]
    return max(values, default="unknown")


def _coverage_rows(
    source: str,
    checks: tuple[str, ...],
    *,
    status: str,
    freshness: str,
    row_count: int,
    notes: str,
) -> list[dict]:
    return [
        {
            "source": source,
            "check": _check_name(check),
            "status": status,
            "freshness": freshness,
            "row_count": row_count,
            "notes": notes,
        }
        for check in checks
    ]


def _error_coverage(
    source: str,
    checks: tuple[str, ...],
    error: Exception,
) -> list[dict]:
    return _coverage_rows(
        source,
        checks,
        status="UNAVAILABLE",
        freshness="unknown",
        row_count=0,
        notes=(
            f"{error.__class__.__name__}; previous findings were preserved because "
            "this source did not refresh"
        ),
    )


def _finding(
    *,
    source: str,
    check: str,
    freshness: str,
    confidence: float,
    blast_radius: str,
    coverage: str,
    reason: str,
    action: str,
    severity: str,
    resource_type: str,
    resource_fields: dict[str, Any],
    evidence: dict[str, Any],
    slo_impact: str = "",
    owner: str = "",
) -> dict:
    return {
        **resource_fields,
        "source": source,
        "check": check,
        "freshness": freshness,
        "confidence": confidence,
        "blast_radius": blast_radius,
        "coverage": coverage,
        "report_only": True,
        "reason": reason,
        "action": action,
        "severity": severity,
        "resource_type": resource_type,
        "likelihood": "OBSERVED",
        "slo_impact": slo_impact,
        "owner": owner,
        **evidence,
    }


def job_duration_rows(
    w: WorkspaceClient,
    warehouse_id: str,
    *,
    workspace_id: str,
    recent_days: int,
    baseline_days: int,
    limit: int,
) -> list[dict]:
    return run_query(
        w,
        load_query("operational_job_duration_regression"),
        warehouse_id,
        {
            "workspace_id": workspace_id,
            "recent_days": recent_days,
            "window_days": recent_days + baseline_days,
            "min_samples": MIN_REGRESSION_SAMPLES,
            "limit": limit,
        },
    )


def job_waste_rows(
    w: WorkspaceClient,
    warehouse_id: str,
    *,
    workspace_id: str,
    recent_days: int,
    limit: int,
) -> list[dict]:
    return run_query(
        w,
        load_query("operational_job_waste"),
        warehouse_id,
        {
            "workspace_id": workspace_id,
            "recent_days": recent_days,
            "limit": limit,
        },
    )


def query_regression_rows(
    w: WorkspaceClient,
    warehouse_id: str,
    *,
    workspace_id: str,
    recent_days: int,
    baseline_days: int,
    limit: int,
) -> list[dict]:
    return run_query(
        w,
        load_query("operational_query_regression"),
        warehouse_id,
        {
            "workspace_id": workspace_id,
            "recent_days": recent_days,
            "window_days": recent_days + baseline_days,
            "min_samples": MIN_REGRESSION_SAMPLES,
            "limit": limit,
        },
    )


def query_scan_rows(
    w: WorkspaceClient,
    warehouse_id: str,
    *,
    workspace_id: str,
    recent_days: int,
    limit: int,
) -> list[dict]:
    return run_query(
        w,
        load_query("operational_query_inefficient_scan"),
        warehouse_id,
        {
            "workspace_id": workspace_id,
            "recent_days": recent_days,
            "min_samples": MIN_SCAN_EXECUTIONS,
            "limit": limit,
        },
    )


def serving_rows(
    w: WorkspaceClient,
    warehouse_id: str,
    *,
    catalog: str,
    schema: str,
    workspace_id: str,
    environment: str,
    recent_days: int,
    baseline_days: int,
    limit: int,
) -> list[dict]:
    catalog = _safe_identifier(catalog)
    schema = _safe_identifier(schema)
    sql = load_query("operational_serving_health").replace(
        "__LLM_USAGE_HOURLY__",
        f"`{catalog}`.`{schema}`.`llm_usage_hourly`",
    )
    return run_query(
        w,
        sql,
        warehouse_id,
        {
            "workspace_id": workspace_id,
            "environment": environment,
            "recent_days": recent_days,
            "window_days": recent_days + baseline_days,
            "limit": limit,
        },
    )


def uc_privileged_grant_rows(
    w: WorkspaceClient,
    warehouse_id: str,
    *,
    catalog: str,
    limit: int,
) -> list[dict]:
    return run_query(
        w,
        load_query("operational_uc_privileged_grants"),
        warehouse_id,
        {"catalog": catalog.lower(), "limit": limit},
    )


def uc_missing_owner_rows(
    w: WorkspaceClient,
    warehouse_id: str,
    *,
    catalog: str,
    limit: int,
) -> list[dict]:
    return run_query(
        w,
        load_query("operational_uc_missing_owners"),
        warehouse_id,
        {"catalog": catalog.lower(), "limit": limit},
    )


def inactive_activity_rows(
    w: WorkspaceClient,
    warehouse_id: str,
    *,
    workspace_id: str,
    days: int,
) -> list[dict]:
    return run_query(
        w,
        load_query("operational_inactive_user_activity"),
        warehouse_id,
        {"workspace_id": workspace_id, "days": days},
    )


def classify_job_duration(rows: list[dict]) -> list[dict]:
    findings: list[dict] = []
    source = "system.lakeflow.job_run_timeline"
    check = _check_name(JOB_DURATION_CHECK)
    for row in rows:
        recent_samples = _integer(row.get("recent_samples"))
        baseline_samples = _integer(row.get("baseline_samples"))
        recent_p95 = _number(row.get("recent_p95_duration_seconds"))
        baseline_p95 = _number(row.get("baseline_p95_duration_seconds"))
        ratio = recent_p95 / baseline_p95 if baseline_p95 > 0 else 0.0
        if (
            recent_samples < MIN_REGRESSION_SAMPLES
            or baseline_samples < MIN_REGRESSION_SAMPLES
            or recent_p95 < MIN_JOB_P95_SECONDS
            or ratio < REGRESSION_RATIO
        ):
            continue
        job_id = str(row.get("job_id") or "")
        job_name = str(row.get("job_name") or job_id)
        freshness = str(row.get("evidence_freshness_at") or "unknown")
        delta_pct = (ratio - 1) * 100
        findings.append(
            _finding(
                source=source,
                check=check,
                freshness=freshness,
                confidence=0.90,
                blast_radius="ONE_JOB",
                coverage=(
                    "PARTIAL: successful JOB_RUN records with populated duration fields; "
                    "duration fields are unavailable for older records"
                ),
                reason=(
                    f"recent p95 duration {recent_p95:.0f}s is {delta_pct:.0f}% above "
                    f"the prior-window p95 {baseline_p95:.0f}s"
                ),
                action="review-job-duration-regression",
                severity="HIGH" if ratio >= 2 else "MEDIUM",
                resource_type="JOB",
                resource_fields={"job_id": job_id, "name": job_name},
                evidence={
                    "recent_p95_duration_seconds": round(recent_p95, 2),
                    "baseline_p95_duration_seconds": round(baseline_p95, 2),
                    "regression_ratio": round(ratio, 3),
                    "recent_samples": recent_samples,
                    "baseline_samples": baseline_samples,
                    "financial_impact_coverage": (
                        "UNSUPPORTED: v1 does not join billing usage to performance signals"
                    ),
                },
                slo_impact=f"job p95 duration increased {delta_pct:.0f}%",
            )
        )
    return findings


def classify_job_waste(rows: list[dict]) -> dict[str, list[dict]]:
    failures: list[dict] = []
    retries: list[dict] = []
    queues: list[dict] = []
    source = "system.lakeflow.job_run_timeline"
    freshness = _source_freshness(rows)
    for row in rows:
        job_id = str(row.get("job_id") or "")
        job_name = str(row.get("job_name") or job_id)
        attempts = _integer(row.get("attempts"))
        failed_attempts = _integer(row.get("failed_attempts"))
        retry_attempts = _integer(row.get("retry_attempts"))
        queue_samples = _integer(row.get("queue_metric_attempts"))
        p95_queue = _number(row.get("p95_queue_seconds"))
        total_queue = _number(row.get("total_queue_seconds"))
        failure_rate = failed_attempts / attempts if attempts else 0.0
        resource = {"job_id": job_id, "name": job_name}
        common = {
            "attempts": attempts,
            "financial_impact_coverage": (
                "UNSUPPORTED: system.lakeflow does not provide billed cost"
            ),
        }
        if failed_attempts >= MIN_FAILED_ATTEMPTS or (
            attempts >= MIN_REGRESSION_SAMPLES and failure_rate >= MIN_FAILURE_RATE
        ):
            failures.append(
                _finding(
                    source=source,
                    check=_check_name(JOB_FAILURE_CHECK),
                    freshness=freshness,
                    confidence=0.95,
                    blast_radius="ONE_JOB",
                    coverage="SUPPORTED: terminal attempt states in the recent window",
                    reason=(
                        f"{failed_attempts} failed terminal attempts out of {attempts} "
                        f"({failure_rate:.1%})"
                    ),
                    action="review-job-failure-causes",
                    severity="HIGH" if failure_rate >= 0.5 else "MEDIUM",
                    resource_type="JOB",
                    resource_fields=resource,
                    evidence={
                        **common,
                        "failed_attempts": failed_attempts,
                        "failure_rate": round(failure_rate, 4),
                    },
                    slo_impact=f"{failed_attempts} failed attempts",
                )
            )
        if retry_attempts >= MIN_RETRY_ATTEMPTS:
            retries.append(
                _finding(
                    source=source,
                    check=_check_name(JOB_RETRY_CHECK),
                    freshness=freshness,
                    confidence=0.90,
                    blast_radius="ONE_JOB",
                    coverage=(
                        "SUPPORTED: retry attempts are repeated terminal rows for one "
                        "workspace/job/run identifier"
                    ),
                    reason=f"{retry_attempts} retry attempts across {attempts} terminal attempts",
                    action="review-job-retry-causes",
                    severity="MEDIUM",
                    resource_type="JOB",
                    resource_fields=resource,
                    evidence={**common, "retry_attempts": retry_attempts},
                    slo_impact=f"{retry_attempts} retry attempts",
                )
            )
        if queue_samples and (
            p95_queue >= MIN_JOB_QUEUE_P95_SECONDS or total_queue >= MIN_JOB_QUEUE_TOTAL_SECONDS
        ):
            queues.append(
                _finding(
                    source=source,
                    check=_check_name(JOB_QUEUE_CHECK),
                    freshness=freshness,
                    confidence=0.90,
                    blast_radius="ONE_JOB",
                    coverage=(
                        "PARTIAL: queue_duration_seconds is populated only for newer "
                        "Lakeflow timeline records"
                    ),
                    reason=(
                        f"p95 queue {p95_queue:.0f}s and total queue "
                        f"{total_queue / 60:.0f}m across {queue_samples} attempts"
                    ),
                    action="review-job-queue-capacity",
                    severity="HIGH" if p95_queue >= 900 else "MEDIUM",
                    resource_type="JOB",
                    resource_fields=resource,
                    evidence={
                        **common,
                        "queue_metric_attempts": queue_samples,
                        "p95_queue_seconds": round(p95_queue, 2),
                        "total_queue_seconds": round(total_queue, 2),
                    },
                    slo_impact=f"job p95 queue is {p95_queue:.0f}s",
                )
            )
    result = {
        JOB_FAILURE_CHECK: failures,
        JOB_RETRY_CHECK: retries,
    }
    if not rows or any(_integer(row.get("queue_metric_attempts")) for row in rows):
        result[JOB_QUEUE_CHECK] = queues
    return result


def classify_query_regressions(rows: list[dict]) -> dict[str, list[dict]]:
    durations: list[dict] = []
    queues: list[dict] = []
    source = "system.query.history"
    check_duration = _check_name(QUERY_DURATION_CHECK)
    check_queue = _check_name(QUERY_QUEUE_CHECK)
    for row in rows:
        recent_samples = _integer(row.get("recent_samples"))
        baseline_samples = _integer(row.get("baseline_samples"))
        fingerprint = str(row.get("query_fingerprint") or "")
        compute_id = str(row.get("compute_id") or "unknown")
        statement_type = str(row.get("statement_type") or "QUERY")
        freshness = str(row.get("evidence_freshness_at") or "unknown")
        resource = {
            "full_name": f"query:{fingerprint}",
            "resource_type": "QUERY",
        }
        common = {
            "query_fingerprint": fingerprint,
            "compute_id": compute_id,
            "statement_type": statement_type,
            "recent_samples": recent_samples,
            "baseline_samples": baseline_samples,
            "query_text_coverage": (
                "UNSUPPORTED: query text is fingerprinted in SQL and never persisted"
            ),
            "financial_impact_coverage": (
                "UNSUPPORTED: v1 does not attribute warehouse cost per statement"
            ),
        }
        recent_p95 = _number(row.get("recent_p95_duration_ms"))
        baseline_p95 = _number(row.get("baseline_p95_duration_ms"))
        duration_ratio = recent_p95 / baseline_p95 if baseline_p95 > 0 else 0.0
        if (
            recent_samples >= MIN_REGRESSION_SAMPLES
            and baseline_samples >= MIN_REGRESSION_SAMPLES
            and recent_p95 >= MIN_QUERY_P95_MS
            and duration_ratio >= REGRESSION_RATIO
        ):
            delta_pct = (duration_ratio - 1) * 100
            durations.append(
                _finding(
                    source=source,
                    check=check_duration,
                    freshness=freshness,
                    confidence=0.80,
                    blast_radius="ONE_QUERY_FINGERPRINT",
                    coverage=(
                        "PARTIAL: exact normalized statement fingerprints only; "
                        "encrypted or empty statement text is excluded"
                    ),
                    reason=(
                        f"recent p95 duration {recent_p95 / 1000:.1f}s is "
                        f"{delta_pct:.0f}% above the prior-window p95 "
                        f"{baseline_p95 / 1000:.1f}s"
                    ),
                    action="review-query-duration-regression",
                    severity="HIGH" if duration_ratio >= 2 else "MEDIUM",
                    resource_type="QUERY",
                    resource_fields=resource,
                    evidence={
                        **common,
                        "recent_p95_duration_ms": round(recent_p95, 2),
                        "baseline_p95_duration_ms": round(baseline_p95, 2),
                        "regression_ratio": round(duration_ratio, 3),
                    },
                    slo_impact=f"query p95 duration increased {delta_pct:.0f}%",
                )
            )
        recent_queue = _number(row.get("recent_p95_queue_ms"))
        baseline_queue = _number(row.get("baseline_p95_queue_ms"))
        queue_ratio = recent_queue / baseline_queue if baseline_queue > 0 else 0.0
        queue_regressed = queue_ratio >= REGRESSION_RATIO
        newly_queued = baseline_queue <= 0 and recent_queue >= MIN_QUERY_QUEUE_P95_MS
        if (
            recent_samples >= MIN_REGRESSION_SAMPLES
            and baseline_samples >= MIN_REGRESSION_SAMPLES
            and recent_queue >= MIN_QUERY_QUEUE_P95_MS
            and (queue_regressed or newly_queued)
        ):
            queues.append(
                _finding(
                    source=source,
                    check=check_queue,
                    freshness=freshness,
                    confidence=0.85,
                    blast_radius="ONE_QUERY_FINGERPRINT",
                    coverage=(
                        "PARTIAL: exact normalized statement fingerprints only; "
                        "queue time measures waiting at compute capacity"
                    ),
                    reason=(
                        f"recent p95 capacity queue {recent_queue / 1000:.1f}s versus "
                        f"{baseline_queue / 1000:.1f}s in the prior window"
                    ),
                    action="review-query-queue-capacity",
                    severity="HIGH" if recent_queue >= 30_000 else "MEDIUM",
                    resource_type="QUERY",
                    resource_fields=resource,
                    evidence={
                        **common,
                        "recent_p95_queue_ms": round(recent_queue, 2),
                        "baseline_p95_queue_ms": round(baseline_queue, 2),
                        "queue_regression_ratio": (
                            round(queue_ratio, 3) if baseline_queue > 0 else None
                        ),
                    },
                    slo_impact=f"query p95 capacity queue is {recent_queue / 1000:.1f}s",
                )
            )
    return {
        QUERY_DURATION_CHECK: durations,
        QUERY_QUEUE_CHECK: queues,
    }


def classify_query_scans(rows: list[dict]) -> list[dict]:
    findings: list[dict] = []
    source = "system.query.history"
    check = _check_name(QUERY_SCAN_CHECK)
    for row in rows:
        executions = _integer(row.get("executions"))
        total_read = _number(row.get("total_read_bytes"))
        output_rows = _integer(row.get("total_output_rows"))
        bytes_per_row = total_read / max(output_rows, 1)
        if (
            executions < MIN_SCAN_EXECUTIONS
            or total_read < MIN_SCAN_BYTES
            or (output_rows > 0 and bytes_per_row < MIN_SCAN_BYTES_PER_OUTPUT_ROW)
        ):
            continue
        fingerprint = str(row.get("query_fingerprint") or "")
        findings.append(
            _finding(
                source=source,
                check=check,
                freshness=str(row.get("evidence_freshness_at") or "unknown"),
                confidence=0.75,
                blast_radius="ONE_QUERY_FINGERPRINT",
                coverage=(
                    "PARTIAL: scan/output ratio is a triage signal, not a query-plan "
                    "diagnosis; cached and encrypted-text statements are excluded"
                ),
                reason=(
                    f"{total_read / 1024**3:.1f} GiB read for {output_rows} output rows "
                    f"across {executions} executions"
                ),
                action="review-query-scan-profile",
                severity="HIGH" if total_read >= 100 * 1024**3 else "MEDIUM",
                resource_type="QUERY",
                resource_fields={"full_name": f"query:{fingerprint}"},
                evidence={
                    "query_fingerprint": fingerprint,
                    "compute_id": str(row.get("compute_id") or "unknown"),
                    "executions": executions,
                    "total_read_bytes": int(total_read),
                    "total_output_rows": output_rows,
                    "bytes_per_output_row": round(bytes_per_row, 2),
                    "query_plan_coverage": (
                        "UNSUPPORTED: v1 does not read query profiles or plans"
                    ),
                    "financial_impact_coverage": (
                        "UNSUPPORTED: v1 does not attribute warehouse cost per statement"
                    ),
                },
            )
        )
    return findings


def classify_serving(rows: list[dict]) -> dict[str, list[dict]]:
    latency: list[dict] = []
    errors: list[dict] = []
    source = "canonical llm_usage_hourly"
    for row in rows:
        requests = _integer(row.get("recent_requests"))
        baseline_requests = _integer(row.get("baseline_requests"))
        endpoint = str(row.get("endpoint") or "unallocated")
        provider = str(row.get("provider") or "unallocated")
        model = str(row.get("model") or "unallocated")
        ledger_source = str(row.get("source") or "unknown")
        freshness = str(row.get("evidence_freshness_at") or "unknown")
        resource = {"endpoint_name": endpoint, "name": endpoint}
        common = {
            "provider": provider,
            "model": model,
            "ledger_source": ledger_source,
            "recent_requests": requests,
            "baseline_requests": baseline_requests,
            "percentile_coverage": (
                "PARTIAL: weighted hourly p95 values; request-level p95 cannot be "
                "reconstructed from the canonical ledger"
            ),
            "financial_impact_coverage": (
                "UNSUPPORTED: serving cost is intentionally not joined into this pack"
            ),
        }
        recent_latency = _number(row.get("recent_weighted_p95_latency_ms"))
        baseline_latency = _number(row.get("baseline_weighted_p95_latency_ms"))
        latency_rows = _integer(row.get("recent_latency_metric_rows"))
        ratio = recent_latency / baseline_latency if baseline_latency > 0 else 0.0
        regressed = (
            baseline_requests >= MIN_SERVING_REQUESTS
            and baseline_latency > 0
            and ratio >= REGRESSION_RATIO
        )
        if (
            requests >= MIN_SERVING_REQUESTS
            and latency_rows > 0
            and recent_latency >= MIN_SERVING_LATENCY_MS
        ):
            comparison = (
                f"{(ratio - 1) * 100:.0f}% above the prior window"
                if regressed
                else f"above the {MIN_SERVING_LATENCY_MS:.0f}ms absolute threshold"
            )
            latency.append(
                _finding(
                    source=source,
                    check=_check_name(SERVING_LATENCY_CHECK),
                    freshness=freshness,
                    confidence=0.75,
                    blast_radius="ONE_SERVING_ENDPOINT",
                    coverage=common["percentile_coverage"],
                    reason=(
                        f"weighted hourly p95 latency {recent_latency:.0f}ms is "
                        f"{comparison}; prior window was {baseline_latency:.0f}ms"
                    ),
                    action="review-serving-latency",
                    severity="HIGH" if recent_latency >= 15_000 else "MEDIUM",
                    resource_type="SERVING_ENDPOINT",
                    resource_fields=resource,
                    evidence={
                        **common,
                        "recent_weighted_p95_latency_ms": round(recent_latency, 2),
                        "baseline_weighted_p95_latency_ms": round(baseline_latency, 2),
                        "latency_regression_ratio": (
                            round(ratio, 3) if baseline_latency > 0 else None
                        ),
                        "recent_latency_metric_rows": latency_rows,
                    },
                    slo_impact=f"serving weighted hourly p95 is {recent_latency:.0f}ms",
                )
            )
        error_rows = _integer(row.get("recent_error_metric_rows"))
        recent_errors = _integer(row.get("recent_errors"))
        error_rate = recent_errors / requests if requests else 0.0
        if (
            requests >= MIN_SERVING_REQUESTS
            and error_rows > 0
            and error_rate >= MIN_SERVING_ERROR_RATE
        ):
            errors.append(
                _finding(
                    source=source,
                    check=_check_name(SERVING_ERROR_CHECK),
                    freshness=freshness,
                    confidence=0.85,
                    blast_radius="ONE_SERVING_ENDPOINT",
                    coverage=("PARTIAL: only canonical sources that populate the errors metric"),
                    reason=(
                        f"{recent_errors} errors across {requests} requests ({error_rate:.1%})"
                    ),
                    action="review-serving-errors",
                    severity="HIGH" if error_rate >= 0.20 else "MEDIUM",
                    resource_type="SERVING_ENDPOINT",
                    resource_fields=resource,
                    evidence={
                        **common,
                        "recent_errors": recent_errors,
                        "error_rate": round(error_rate, 4),
                        "recent_error_metric_rows": error_rows,
                    },
                    slo_impact=f"serving error rate is {error_rate:.1%}",
                )
            )
    result: dict[str, list[dict]] = {}
    if not rows or any(_integer(row.get("recent_latency_metric_rows")) for row in rows):
        result[SERVING_LATENCY_CHECK] = latency
    if not rows or any(_integer(row.get("recent_error_metric_rows")) for row in rows):
        result[SERVING_ERROR_CHECK] = errors
    return result


def classify_uc_grants(rows: list[dict]) -> list[dict]:
    findings: list[dict] = []
    for row in rows:
        full_name = ".".join(
            str(row.get(key) or "") for key in ("table_catalog", "table_schema", "table_name")
        )
        privilege = str(row.get("privilege_type") or "")
        inherited = str(row.get("inherited_from") or "direct")
        findings.append(
            _finding(
                source="system.information_schema.table_privileges",
                check=_check_name(UC_GRANT_CHECK),
                freshness="query-time metadata snapshot",
                confidence=0.95,
                blast_radius="ONE_TABLE_ALL_ACCOUNT_USERS",
                coverage=(
                    "PARTIAL: one configured catalog, privilege-filtered information_schema; "
                    "MANAGE visibility has a documented limitation"
                ),
                reason=(
                    f"`account users` has {privilege} on {full_name} (inherited from {inherited})"
                ),
                action="review-broad-privileged-grant",
                severity="HIGH",
                resource_type="TABLE",
                resource_fields={"full_name": full_name},
                evidence={
                    "grantee": str(row.get("grantee") or ""),
                    "privilege_type": privilege,
                    "inherited_from": inherited,
                    "catalog_scope": str(row.get("table_catalog") or ""),
                    "read_only_grants_coverage": (
                        "UNSUPPORTED: v1 intentionally does not flag SELECT or BROWSE"
                    ),
                },
                owner=str(row.get("table_owner") or ""),
            )
        )
    return findings


def classify_uc_missing_owners(rows: list[dict]) -> list[dict]:
    findings: list[dict] = []
    for row in rows:
        full_name = ".".join(
            str(row.get(key) or "") for key in ("table_catalog", "table_schema", "table_name")
        )
        findings.append(
            _finding(
                source="system.information_schema.tables",
                check=_check_name(UC_OWNER_CHECK),
                freshness="query-time metadata snapshot",
                confidence=1.0,
                blast_radius="ONE_TABLE",
                coverage=(
                    "PARTIAL: defensive empty-owner consistency check in one configured "
                    "catalog; Unity Catalog declares TABLE_OWNER non-null"
                ),
                reason=f"{full_name} has no non-empty TABLE_OWNER metadata",
                action="review-table-owner",
                severity="HIGH",
                resource_type="TABLE",
                resource_fields={"full_name": full_name},
                evidence={
                    "catalog_scope": str(row.get("table_catalog") or ""),
                    "creator_principal": str(row.get("creator_principal") or ""),
                },
            )
        )
    return findings


def normalize_pat_findings(rows: list[dict]) -> list[dict]:
    findings: list[dict] = []
    for row in rows:
        token_id = str(row.get("token_id") or "")
        resource_id = _stable_resource("pat", token_id)
        issues = str(row.get("issues") or "PAT hygiene issue")
        over_age = bool(row.get("over_age"))
        findings.append(
            _finding(
                source="workspace token_management API",
                check=_check_name(PAT_TOKEN_CHECK),
                freshness="query-time privileged inventory",
                confidence=0.95,
                blast_radius="ONE_CREDENTIAL",
                coverage=(
                    "PARTIAL: current workspace PAT inventory only; OAuth tokens and "
                    "service-principal secrets are not included"
                ),
                reason=issues,
                action="review-token-revocation",
                severity=("CRITICAL" if over_age or "never expires" in issues.lower() else "HIGH"),
                resource_type="TOKEN",
                resource_fields={
                    "resource_id": resource_id,
                    "name": resource_id,
                },
                evidence={
                    # The global App viewer masker redacts this structured key.
                    "token_id": token_id,
                    "created_by": str(row.get("created_by") or ""),
                    "age_days": _integer(row.get("age_days")),
                    "issues": issues,
                    "over_age": over_age,
                    "token_comment_coverage": (
                        "UNSUPPORTED: token comments are intentionally not persisted"
                    ),
                },
                owner=str(row.get("created_by") or ""),
            )
        )
    return findings


def normalize_inactive_user_findings(rows: list[dict]) -> list[dict]:
    findings: list[dict] = []
    for row in rows:
        user_name = str(row.get("user_name") or "")
        resource_id = _stable_resource("user", user_name)
        findings.append(
            _finding(
                source="workspace SCIM users + system.access.audit",
                check=_check_name(INACTIVE_USER_CHECK),
                freshness="query-time SCIM inventory and bounded audit window",
                confidence=0.80,
                blast_radius="ONE_IDENTITY",
                coverage=(
                    "PARTIAL: no recorded audit event is not proof that the identity is "
                    "unused outside the captured workspace event surface"
                ),
                reason=str(row.get("reason") or "no audited activity"),
                action="review-user-access",
                severity="MEDIUM",
                resource_type="USER",
                resource_fields={
                    "resource_id": resource_id,
                    "name": resource_id,
                },
                evidence={
                    # These structured identity keys are viewer-redacted by the App.
                    "user_name": user_name,
                    "display_name": str(row.get("display_name") or ""),
                    "deactivation_coverage": (
                        "UNSUPPORTED: this report never deactivates users or calls the IdP"
                    ),
                },
                owner=user_name,
            )
        )
    return findings


def collect_findings(
    w: WorkspaceClient,
    warehouse_id: str,
    *,
    catalog: str,
    schema: str,
    workspace_id: str,
    environment: str,
    recent_days: int = DEFAULT_RECENT_DAYS,
    baseline_days: int = DEFAULT_BASELINE_DAYS,
    limit: int = DEFAULT_LIMIT,
    now_ms: int | None = None,
    token_max_age_days: int = 90,
    token_expiry_warn_days: int = 14,
    inactive_user_days: int = 90,
    query: Callable[..., list[dict]] | None = None,
) -> tuple[dict[str, list[dict]], list[dict]]:
    """Collect source-isolated operational findings plus explicit coverage.

    ``query`` is a test seam. Production callers use the module's trusted
    ``run_query``-based fetch functions.
    """

    _validate_window(recent_days, baseline_days, limit)
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    findings: dict[str, list[dict]] = {}
    coverage: list[dict] = []

    def invoke(fetch: Callable[..., list[dict]], **kwargs) -> list[dict]:
        if query is None:
            return fetch(w, warehouse_id, **kwargs)
        return query(fetch.__name__, **kwargs)

    lakeflow_source = "system.lakeflow.job_run_timeline"
    try:
        rows = invoke(
            job_duration_rows,
            workspace_id=workspace_id,
            recent_days=recent_days,
            baseline_days=baseline_days,
            limit=limit,
        )
    except Exception as error:  # noqa: BLE001 - source isolation is intentional
        coverage.extend(_error_coverage(lakeflow_source, (JOB_DURATION_CHECK,), error))
    else:
        findings[JOB_DURATION_CHECK] = classify_job_duration(rows)
        coverage.extend(
            _coverage_rows(
                lakeflow_source,
                (JOB_DURATION_CHECK,),
                status="PARTIAL",
                freshness=_source_freshness(rows),
                row_count=len(rows),
                notes=(
                    "Successful JOB_RUN p95 only; duration fields are populated only "
                    "for newer timeline records"
                ),
            )
        )

    waste_checks = (JOB_FAILURE_CHECK, JOB_RETRY_CHECK, JOB_QUEUE_CHECK)
    try:
        rows = invoke(
            job_waste_rows,
            workspace_id=workspace_id,
            recent_days=recent_days,
            limit=limit,
        )
    except Exception as error:  # noqa: BLE001 - source isolation is intentional
        coverage.extend(_error_coverage(lakeflow_source, waste_checks, error))
    else:
        classified = classify_job_waste(rows)
        findings.update(classified)
        coverage.extend(
            _coverage_rows(
                lakeflow_source,
                (JOB_FAILURE_CHECK, JOB_RETRY_CHECK),
                status="SUPPORTED",
                freshness=_source_freshness(rows),
                row_count=len(rows),
                notes="Terminal result states and repeated run IDs; cost impact unsupported",
            )
        )
        queue_supported = JOB_QUEUE_CHECK in classified
        coverage.extend(
            _coverage_rows(
                lakeflow_source,
                (JOB_QUEUE_CHECK,),
                status="PARTIAL" if queue_supported else "UNSUPPORTED",
                freshness=_source_freshness(rows),
                row_count=len(rows),
                notes=(
                    "Queue duration is available only on newer timeline rows"
                    if queue_supported
                    else "No populated queue_duration_seconds metric; prior findings preserved"
                ),
            )
        )

    query_source = "system.query.history"
    regression_checks = (QUERY_DURATION_CHECK, QUERY_QUEUE_CHECK)
    try:
        rows = invoke(
            query_regression_rows,
            workspace_id=workspace_id,
            recent_days=recent_days,
            baseline_days=baseline_days,
            limit=limit,
        )
    except Exception as error:  # noqa: BLE001 - source isolation is intentional
        coverage.extend(_error_coverage(query_source, regression_checks, error))
    else:
        findings.update(classify_query_regressions(rows))
        coverage.extend(
            _coverage_rows(
                query_source,
                regression_checks,
                status="PARTIAL",
                freshness=_source_freshness(rows),
                row_count=len(rows),
                notes=(
                    "Completed statements grouped by exact normalized-text fingerprint; "
                    "empty/encrypted statement text is excluded"
                ),
            )
        )

    try:
        rows = invoke(
            query_scan_rows,
            workspace_id=workspace_id,
            recent_days=recent_days,
            limit=limit,
        )
    except Exception as error:  # noqa: BLE001 - source isolation is intentional
        coverage.extend(_error_coverage(query_source, (QUERY_SCAN_CHECK,), error))
    else:
        findings[QUERY_SCAN_CHECK] = classify_query_scans(rows)
        coverage.extend(
            _coverage_rows(
                query_source,
                (QUERY_SCAN_CHECK,),
                status="PARTIAL",
                freshness=_source_freshness(rows),
                row_count=len(rows),
                notes=(
                    "Scan/output ratio only; cached statements, encrypted text, query plans, "
                    "and cost attribution are excluded"
                ),
            )
        )

    serving_source = f"{catalog}.{schema}.llm_usage_hourly"
    serving_checks = (SERVING_LATENCY_CHECK, SERVING_ERROR_CHECK)
    try:
        rows = invoke(
            serving_rows,
            catalog=catalog,
            schema=schema,
            workspace_id=workspace_id,
            environment=environment,
            recent_days=recent_days,
            baseline_days=baseline_days,
            limit=limit,
        )
    except Exception as error:  # noqa: BLE001 - source isolation is intentional
        coverage.extend(_error_coverage(serving_source, serving_checks, error))
    else:
        fresh_rows = [
            row
            for row in rows
            if _number(row.get("freshness_age_hours"), default=float("inf"))
            <= MAX_LEDGER_FRESHNESS_HOURS
        ]
        if rows and not fresh_rows:
            freshness_age = min(
                _number(row.get("freshness_age_hours"), default=float("inf")) for row in rows
            )
            coverage.extend(
                _coverage_rows(
                    serving_source,
                    serving_checks,
                    status="UNAVAILABLE",
                    freshness=_source_freshness(rows),
                    row_count=len(rows),
                    notes=(
                        f"ledger is {freshness_age:.0f}h old; prior findings preserved "
                        f"(maximum {MAX_LEDGER_FRESHNESS_HOURS:.0f}h)"
                    ),
                )
            )
        else:
            classified = classify_serving(fresh_rows)
            findings.update(classified)
            for check in serving_checks:
                supported = check in classified
                coverage.extend(
                    _coverage_rows(
                        serving_source,
                        (check,),
                        status="PARTIAL" if supported else "UNSUPPORTED",
                        freshness=_source_freshness(fresh_rows),
                        row_count=len(fresh_rows),
                        notes=(
                            "Canonical hourly aggregates; request-level percentiles and "
                            "sources with null or stale latency/error metrics are not covered"
                            if supported
                            else "Required canonical metric is not populated; prior findings "
                            "preserved"
                        ),
                    )
                )

    pat_source = "workspace token_management API"
    try:
        tokens = security.fetch_tokens(w)
    except Exception as error:  # noqa: BLE001 - privileged source isolation
        coverage.extend(_error_coverage(pat_source, (PAT_TOKEN_CHECK,), error))
    else:
        token_rows = security.classify_tokens(
            tokens,
            now_ms,
            token_max_age_days,
            token_expiry_warn_days,
        )
        findings[PAT_TOKEN_CHECK] = normalize_pat_findings(token_rows)
        coverage.extend(
            _coverage_rows(
                pat_source,
                (PAT_TOKEN_CHECK,),
                status="PARTIAL",
                freshness="query-time privileged inventory",
                row_count=len(tokens),
                notes=(
                    "Requires privileged workspace token listing; token comments are not "
                    "persisted and prior findings resolve only after a successful listing"
                ),
            )
        )

    inactive_source = "workspace SCIM users + system.access.audit"
    try:
        users = security.fetch_workspace_users(w)
        activity = invoke(
            inactive_activity_rows,
            workspace_id=workspace_id,
            days=inactive_user_days,
        )
    except Exception as error:  # noqa: BLE001 - joined source isolation
        coverage.extend(_error_coverage(inactive_source, (INACTIVE_USER_CHECK,), error))
    else:
        inactive_rows = security.find_inactive_users(
            users,
            activity,
            inactive_user_days,
        )
        findings[INACTIVE_USER_CHECK] = normalize_inactive_user_findings(inactive_rows)
        coverage.extend(
            _coverage_rows(
                inactive_source,
                (INACTIVE_USER_CHECK,),
                status="PARTIAL",
                freshness="query-time SCIM inventory and bounded audit window",
                row_count=len(users),
                notes=(
                    "Active users with no captured workspace audit event; identities are "
                    "structured for the existing viewer redaction boundary"
                ),
            )
        )

    uc_scope_note = f"configured catalog {catalog!r} only; information_schema is privilege-filtered"
    try:
        rows = invoke(
            uc_privileged_grant_rows,
            catalog=catalog,
            limit=limit,
        )
    except Exception as error:  # noqa: BLE001 - source isolation is intentional
        coverage.extend(
            _error_coverage(
                "system.information_schema.table_privileges",
                (UC_GRANT_CHECK,),
                error,
            )
        )
    else:
        findings[UC_GRANT_CHECK] = classify_uc_grants(rows)
        coverage.extend(
            _coverage_rows(
                "system.information_schema.table_privileges",
                (UC_GRANT_CHECK,),
                status="PARTIAL",
                freshness="query-time metadata snapshot",
                row_count=len(rows),
                notes=(
                    f"{uc_scope_note}; flags only account users with MODIFY, MANAGE, "
                    "or ALL PRIVILEGES"
                ),
            )
        )

    try:
        rows = invoke(
            uc_missing_owner_rows,
            catalog=catalog,
            limit=limit,
        )
    except Exception as error:  # noqa: BLE001 - source isolation is intentional
        coverage.extend(
            _error_coverage(
                "system.information_schema.tables",
                (UC_OWNER_CHECK,),
                error,
            )
        )
    else:
        findings[UC_OWNER_CHECK] = classify_uc_missing_owners(rows)
        coverage.extend(
            _coverage_rows(
                "system.information_schema.tables",
                (UC_OWNER_CHECK,),
                status="PARTIAL",
                freshness="query-time metadata snapshot",
                row_count=len(rows),
                notes=(f"{uc_scope_note}; defensive only because TABLE_OWNER is declared non-null"),
            )
        )

    return findings, coverage


def _safe_identifier(value: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"Unsafe Unity Catalog identifier: {value!r}")
    return value


def _validate_window(recent_days: int, baseline_days: int, limit: int) -> None:
    if not 1 <= recent_days <= 30:
        raise ValueError("recent_days must be between 1 and 30")
    if not 7 <= baseline_days <= 90:
        raise ValueError("baseline_days must be between 7 and 90")
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")

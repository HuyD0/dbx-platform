"""AI-summarized platform health digest.

``collect_findings`` re-runs the pure checks from every area and
``summarize`` turns them into a prioritized executive summary with a single
``ai_query()`` call on the SQL warehouse — Databricks-hosted foundation
models, no extra credentials or SDK surface. The digest is garnish: when the
model endpoint is unavailable the raw findings still get reported and stored.

Prompt construction is pure and unit-tested; only the collection and the
ai_query/INSERT calls touch the workspace.
"""

from __future__ import annotations

import json
import re
from importlib import resources

from databricks.sdk import WorkspaceClient

from dbx_platform import cost, governance, housekeeping, ml, security
from dbx_platform.config import Settings
from dbx_platform.system_tables import run_query

_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9._/-]+$")

FINDINGS_SCHEMA = (
    "array<struct<area:string,check_name:string,resource:string,"
    "reason:string,action:string,details:string>>"
)


# --- collection ---------------------------------------------------------------

def collect_findings(
    w: WorkspaceClient, s: Settings, warehouse_id: str, now_ms: int, days: int
) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """Run every area's pure checks. Returns (findings by 'area/check',
    skipped checks with the reason). One unavailable surface never kills
    the digest."""
    findings: dict[str, list[dict]] = {}
    skipped: dict[str, str] = {}

    def collect(key: str, fn) -> None:
        try:
            findings[key] = fn()
        except Exception as e:  # noqa: BLE001 — digest must survive any one check
            skipped[key] = str(e)

    collect("housekeeping/stale-clusters", lambda: housekeeping.classify_clusters(
        housekeeping.fetch_clusters(w), now_ms, s.stale_cluster_days, s.max_uptime_hours))
    collect("housekeeping/orphaned-jobs", lambda: housekeeping.find_orphaned_jobs(
        housekeeping.fetch_jobs(w), housekeeping.fetch_active_principals(w)))
    collect("housekeeping/jobs-on-all-purpose", lambda: housekeeping.find_jobs_on_all_purpose(
        housekeeping.fetch_jobs_with_clusters(w), s.allpurpose_fixed_workers_max))
    collect("security/token-audit", lambda: security.classify_tokens(
        security.fetch_tokens(w), now_ms, s.token_max_age_days, s.token_expiry_warn_days))
    collect("governance/policy-drift", lambda: _policy_drift(w))
    collect("governance/tag-compliance", lambda: governance.find_missing_tags(
        governance.fetch_taggable_resources(w), s.required_tag_list()))
    collect("ml/endpoint-audit", lambda: ml.classify_serving_endpoints(
        ml.fetch_serving_endpoints(w), now_ms, s.serving_failed_grace_hours))
    collect("ml/gpu-audit", lambda: ml.classify_gpu_clusters(
        ml.fetch_clusters_with_node_types(w), ml.fetch_gpu_node_types(w),
        now_ms, s.gpu_max_uptime_hours))
    collect("ml/vector-search-audit", lambda: ml.find_vector_search_findings(
        ml.fetch_vector_search(w), now_ms, s.vector_search_grace_hours))
    collect("cost/cluster-utilization", lambda: cost.classify_cluster_utilization(
        cost.cluster_utilization(w, warehouse_id, days),
        s.util_cpu_threshold_pct, s.util_mem_threshold_pct))
    collect("cost/warehouse-utilization", lambda: cost.classify_warehouse_utilization(
        cost.warehouse_utilization(w, warehouse_id, days),
        s.warehouse_min_queries, s.warehouse_queue_warn_seconds))
    collect("cost/failed-run-waste",
            lambda: cost.failed_run_waste(w, warehouse_id, days, 20))
    return findings, skipped


def _policy_drift(w: WorkspaceClient) -> list[dict]:
    packaged = resources.files("dbx_platform") / "policies"
    plan = governance.diff_policies(
        governance.load_local_policies(str(packaged)), governance.fetch_remote_policies(w)
    )
    return (
        [{"action": "create", "name": p["name"]} for p in plan["create"]]
        + [{"action": "update", "name": p["name"]} for p in plan["update"]]
    )


# --- prompt (pure) ------------------------------------------------------------

def build_digest_prompt(
    findings_by_check: dict[str, list[dict]], skipped: dict[str, str], days: int
) -> str:
    """Deterministic digest prompt. Pure function."""
    total = sum(len(v) for v in findings_by_check.values())
    payload = json.dumps(
        {k: v for k, v in sorted(findings_by_check.items()) if v},
        default=str, separators=(",", ":"), sort_keys=True,
    )
    skipped_note = (
        "Checks that could not run (say so briefly): "
        + ", ".join(sorted(skipped)) + ".\n"
        if skipped
        else ""
    )
    return (
        "You are summarizing automated Databricks platform checks for the "
        "platform team's weekly digest.\n"
        f"Window: last {days} days. Total findings: {total}.\n"
        "Write a concise executive summary in markdown:\n"
        "1. Lead with the biggest money leaks (use the cost figures given; "
        "never invent numbers).\n"
        "2. Always surface security findings, even minor ones.\n"
        "3. Group the rest by theme, at most ten bullets total.\n"
        "4. End with the top three recommended actions, each naming the "
        "owning team or person from the findings.\n"
        "5. If there are no findings, say clearly that no action is needed.\n"
        + skipped_note
        + "Findings JSON (check name -> finding rows):\n"
        + payload
    )


def flatten_findings(findings_by_check: dict[str, list[dict]]) -> list[dict]:
    """Findings as flat rows for the platform_findings table. Pure function."""
    rows = []
    for key, items in sorted(findings_by_check.items()):
        area, _, check = key.partition("/")
        for f in items:
            resource = str(
                f.get("name") or f.get("full_name") or f.get("cluster_name")
                or f.get("cluster_id") or f.get("warehouse_id") or f.get("job_id") or ""
            )
            rows.append(
                {
                    "area": area,
                    "check_name": check,
                    "resource": resource,
                    "reason": str(f.get("reason", "")),
                    "action": str(f.get("action", "")),
                    "details": json.dumps(f, default=str, sort_keys=True),
                }
            )
    return rows


# --- summarize & store --------------------------------------------------------

def summarize(w: WorkspaceClient, warehouse_id: str, model: str, prompt: str) -> str:
    """One ai_query() call against a Databricks-hosted foundation model."""
    if not _MODEL_NAME_RE.match(model):
        raise ValueError(f"invalid model endpoint name: {model!r}")
    rows = run_query(
        w,
        f"SELECT ai_query('{model}', :prompt) AS digest",
        warehouse_id,
        {"prompt": prompt},
    )
    if not rows or not rows[0].get("digest"):
        raise RuntimeError(f"ai_query on '{model}' returned no result")
    return rows[0]["digest"]


def store_digest(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    days: int,
    model: str,
    digest: str,
    findings_by_check: dict[str, list[dict]],
) -> None:
    fq = f"{catalog}.{schema}"
    run_query(
        w,
        f"INSERT INTO {fq}.platform_digest (run_ts, days, model, digest, findings_json) "
        "VALUES (current_timestamp(), :days, :model, :digest, :findings_json)",
        warehouse_id,
        {
            "days": days,
            "model": model,
            "digest": digest,
            "findings_json": json.dumps(findings_by_check, default=str, sort_keys=True),
        },
    )
    rows = flatten_findings(findings_by_check)
    if rows:
        run_query(
            w,
            f"INSERT INTO {fq}.platform_findings "
            "(run_ts, area, check_name, resource, reason, action, details) "
            "SELECT current_timestamp(), item.area, item.check_name, item.resource, "
            "item.reason, item.action, item.details "
            f"FROM (SELECT explode(from_json(:findings, '{FINDINGS_SCHEMA}')) AS item)",
            warehouse_id,
            {"findings": json.dumps(rows, default=str)},
        )

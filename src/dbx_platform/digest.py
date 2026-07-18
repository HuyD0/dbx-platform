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

import hashlib
import json
import re
from importlib import resources

from databricks.sdk import WorkspaceClient

from dbx_platform import cost, governance, housekeeping, llm_cost, ml, security
from dbx_platform.config import Settings
from dbx_platform.system_tables import run_query

_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9._/-]+$")

FINDINGS_SCHEMA = (
    "array<struct<finding_id:string,workspace_id:string,environment:string,"
    "area:string,check_name:string,resource:string,reason:string,action:string,"
    "details:string,pillar:string,severity:string,likelihood:string,"
    "financial_impact_usd:double,slo_impact:string,confidence:double,owner:string,"
    "affected_resources_json:string,evidence_json:string,state:string,"
    "proposed_action_type:string,blast_radius:string,freshness_at:string>>"
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
    collect(
        "cost/llm-efficiency",
        lambda: _llm_efficiency_findings(w, warehouse_id, days),
    )
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


def _llm_efficiency_findings(
    w: WorkspaceClient, warehouse_id: str, days: int
) -> list[dict]:
    try:
        cost_rows = llm_cost.databricks_cost(
            w, warehouse_id, days, gateway_enriched=True
        )
    except Exception:  # noqa: BLE001 - compatibility schema
        cost_rows = llm_cost.databricks_cost(
            w, warehouse_id, days, gateway_enriched=False
        )
    try:
        usage_rows = llm_cost.gateway_usage(w, warehouse_id, min(days, 90))
    except Exception:  # noqa: BLE001 - compatibility schema
        usage_rows = llm_cost.endpoint_usage(w, warehouse_id, min(days, 90))
    costs = llm_cost.normalize_cost_rows(
        cost_rows, "system.billing.usage", "DATABRICKS_LIST"
    )
    usage = llm_cost.normalize_usage_rows(usage_rows, "model usage")
    return llm_cost.efficiency(costs, usage)["recommendations"]


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


def flatten_findings(
    findings_by_check: dict[str, list[dict]],
    *,
    workspace_id: str = "current",
    environment: str = "prod",
) -> list[dict]:
    """Normalize heterogeneous observations into canonical Finding rows."""

    rows = []
    for key, items in sorted(findings_by_check.items()):
        area, _, check = key.partition("/")
        for f in items:
            resource = str(
                f.get("name") or f.get("full_name") or f.get("cluster_name")
                or f.get("cluster_id") or f.get("warehouse_id") or f.get("job_id") or ""
            )
            resource_id = str(
                f.get("resource_id")
                or f.get("cluster_id")
                or f.get("warehouse_id")
                or f.get("job_id")
                or f.get("token_id")
                or f.get("endpoint_name")
                or f.get("full_name")
                or resource
            )
            action = str(f.get("action") or f.get("action_type") or "review")
            if action == "permanent-delete":
                action = "review-deletion-candidate"
            evidence = json.dumps(f, default=str, sort_keys=True)
            finding_key = {
                "workspace_id": workspace_id,
                "environment": environment,
                "area": area,
                "check_name": check,
                "resource_id": resource_id,
                "action": action,
            }
            finding_id = hashlib.sha256(
                json.dumps(
                    finding_key,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            severity = _finding_severity(area, action, f)
            confidence = _finding_confidence(f.get("confidence"))
            impact = _finding_financial_impact(area, f)
            owner = str(
                f.get("owner")
                or f.get("creator")
                or f.get("created_by")
                or f.get("team")
                or ""
            )
            affected = [
                {
                    "resource_type": str(
                        f.get("resource_type")
                        or f.get("type")
                        or _resource_type(check)
                    ).upper(),
                    "resource_id": resource_id,
                    "display_name": resource,
                }
            ] if resource_id else []
            rows.append(
                {
                    "finding_id": finding_id,
                    "workspace_id": workspace_id,
                    "environment": environment,
                    "area": area,
                    "check_name": check,
                    "resource": resource,
                    "reason": str(f.get("reason", "")),
                    "action": action,
                    "details": evidence,
                    "pillar": _pillar(area, check),
                    "severity": severity,
                    "likelihood": str(f.get("likelihood") or "OBSERVED").upper(),
                    "financial_impact_usd": impact,
                    "slo_impact": str(f.get("slo_impact") or ""),
                    "confidence": confidence,
                    "owner": owner,
                    "affected_resources_json": json.dumps(
                        affected,
                        sort_keys=True,
                    ),
                    "evidence_json": evidence,
                    "state": "OPEN",
                    "proposed_action_type": action,
                    "blast_radius": (
                        str(f.get("blast_radius") or "ONE_RESOURCE").upper()
                        if affected
                        else "UNKNOWN"
                    ),
                    "freshness_at": _finding_freshness(f),
                }
            )
    return rows


def _finding_freshness(finding: dict) -> str | None:
    """Return source-evidence time when it resembles an ISO date/timestamp."""

    value = (
        finding.get("freshness_at")
        or finding.get("evidence_freshness_at")
        or finding.get("freshness")
    )
    if value is None:
        return None
    text = str(value).strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}(?:[T ][0-9:.+-]+Z?)?$", text):
        return None
    return text


def _pillar(area: str, check: str) -> str:
    if area in {"cost", "security", "performance"}:
        return area.upper()
    if area == "housekeeping" and check in {"jobs-on-all-purpose"}:
        return "COST"
    if area == "ml" and ("serving" in check or "endpoint" in check):
        return "PERFORMANCE"
    return "RISK"


def _resource_type(check: str) -> str:
    for marker, kind in (
        ("cluster", "CLUSTER"),
        ("warehouse", "WAREHOUSE"),
        ("job", "JOB"),
        ("token", "TOKEN"),
        ("endpoint", "SERVING_ENDPOINT"),
        ("model", "MODEL"),
        ("policy", "CLUSTER_POLICY"),
    ):
        if marker in check:
            return kind
    return "RESOURCE"


def _finding_severity(area: str, action: str, finding: dict) -> str:
    explicit = str(finding.get("severity") or "").upper()
    if explicit in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}:
        return explicit
    if area == "security" and ("revoke" in action or finding.get("over_age")):
        return "CRITICAL"
    if area == "security":
        return "HIGH"
    if action in {"terminate", "review-deletion-candidate"}:
        return "HIGH"
    if _finding_financial_impact(area, finding) >= 500:
        return "HIGH"
    return "MEDIUM"


def _finding_confidence(value) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(float(value), 1.0))
    return {
        "high": 0.9,
        "medium": 0.65,
        "low": 0.4,
    }.get(str(value or "").lower(), 0.75)


def _finding_financial_impact(area: str, finding: dict) -> float:
    if area != "cost":
        return 0.0
    for key in (
        "estimated_savings_usd",
        "wasted_cost_usd",
        "list_cost_usd",
        "cost_usd",
        "cost",
    ):
        try:
            if finding.get(key) is not None:
                return max(float(finding[key]), 0.0)
        except (TypeError, ValueError):
            continue
    return 0.0


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
    *,
    workspace_id: str | None = None,
    environment: str | None = None,
) -> None:
    fq = f"{catalog}.{schema}"
    workspace_id = workspace_id or str(w.get_workspace_id())
    environment = environment or Settings.from_env().environment
    run_query(
        w,
        f"INSERT INTO {fq}.platform_digest "
        "(run_ts, workspace_id, environment, days, model, digest, findings_json) "
        "VALUES (current_timestamp(), :workspace_id, :environment, :days, :model, "
        ":digest, :findings_json)",
        warehouse_id,
        {
            "workspace_id": workspace_id,
            "environment": environment,
            "days": days,
            "model": model,
            "digest": digest,
            "findings_json": json.dumps(findings_by_check, default=str, sort_keys=True),
        },
    )
    store_findings(
        w,
        warehouse_id,
        catalog,
        schema,
        findings_by_check,
        workspace_id=workspace_id,
        environment=environment,
    )


def store_findings(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    findings_by_check: dict[str, list[dict]],
    *,
    workspace_id: str | None = None,
    environment: str | None = None,
) -> int:
    """Upsert canonical findings and resolve disappeared rows per fresh check."""

    fq = f"{catalog}.{schema}"
    workspace_id = workspace_id or str(w.get_workspace_id())
    environment = environment or Settings.from_env().environment
    rows = flatten_findings(
        findings_by_check,
        workspace_id=workspace_id,
        environment=environment,
    )
    if rows:
        run_query(
            w,
            f"""MERGE INTO {fq}.platform_findings AS target
USING (
  SELECT item.*
  FROM (SELECT explode(from_json(:findings, '{FINDINGS_SCHEMA}')) AS item)
) AS source
ON target.finding_id = source.finding_id
  AND target.workspace_id = source.workspace_id
  AND target.environment = source.environment
WHEN MATCHED THEN UPDATE SET
  run_ts = current_timestamp(),
  area = source.area,
  check_name = source.check_name,
  resource = source.resource,
  reason = source.reason,
  action = source.action,
  details = source.details,
  pillar = source.pillar,
  severity = source.severity,
  likelihood = source.likelihood,
  financial_impact_usd = source.financial_impact_usd,
  slo_impact = source.slo_impact,
  confidence = source.confidence,
  owner = source.owner,
  affected_resources_json = source.affected_resources_json,
  evidence_json = source.evidence_json,
  freshness_at = COALESCE(TRY_CAST(source.freshness_at AS TIMESTAMP), current_timestamp()),
  last_seen_at = current_timestamp(),
  state = source.state,
  proposed_action_type = source.proposed_action_type,
  blast_radius = source.blast_radius
WHEN NOT MATCHED THEN INSERT (
  run_ts, area, check_name, resource, reason, action, details,
  finding_id, workspace_id, environment, pillar, severity, likelihood,
  financial_impact_usd, slo_impact, confidence, owner,
  affected_resources_json, evidence_json, freshness_at, first_seen_at,
  last_seen_at, state, proposed_action_type, blast_radius
) VALUES (
  current_timestamp(), source.area, source.check_name, source.resource,
  source.reason, source.action, source.details, source.finding_id,
  source.workspace_id, source.environment, source.pillar, source.severity,
  source.likelihood, source.financial_impact_usd, source.slo_impact,
  source.confidence, source.owner, source.affected_resources_json,
  source.evidence_json,
  COALESCE(TRY_CAST(source.freshness_at AS TIMESTAMP), current_timestamp()),
  current_timestamp(),
  current_timestamp(), source.state, source.proposed_action_type,
  source.blast_radius
)""",
            warehouse_id,
            {"findings": json.dumps(rows, default=str)},
        )
    ids_by_check: dict[tuple[str, str], list[str]] = {}
    for key in findings_by_check:
        area, _, check = key.partition("/")
        ids_by_check[(area, check)] = [
            str(row["finding_id"])
            for row in rows
            if row["area"] == area and row["check_name"] == check
        ]
    for (area, check), finding_ids in ids_by_check.items():
        run_query(
            w,
            f"""UPDATE {fq}.platform_findings
SET state = 'RESOLVED',
    freshness_at = current_timestamp()
WHERE workspace_id = :workspace_id
  AND environment = :environment
  AND area = :area
  AND check_name = :check_name
  AND state = 'OPEN'
  AND finding_id NOT IN (
    SELECT finding_id
    FROM (
      SELECT explode(from_json(:finding_ids, 'array<string>')) AS finding_id
    )
  )""",
            warehouse_id,
            {
                "workspace_id": workspace_id,
                "environment": environment,
                "area": area,
                "check_name": check,
                "finding_ids": json.dumps(finding_ids),
            },
        )
    return len(rows)

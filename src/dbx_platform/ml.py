"""AI/ML workload management: model serving, registry hygiene, GPU, vector search.

Each check is split into *fetch* (thin SDK wrapper returning plain dicts) and
*decide* (pure function — unit-testable offline). Every check here is
report-only: serving-endpoint config changes trigger a full redeployment and
model/endpoint deletion is irreversible, so remediation stays a human action
(see docs/runbook.md).
"""

from __future__ import annotations

from databricks.sdk import WorkspaceClient

from dbx_platform.system_tables import load_query, run_query

MS_PER_DAY = 86_400_000
MS_PER_HOUR = 3_600_000


# --- model serving endpoints ------------------------------------------------

def fetch_serving_endpoints(w: WorkspaceClient) -> list[dict]:
    out = []
    for summary in w.serving_endpoints.list():
        e = w.serving_endpoints.get(summary.name)
        config = e.config
        gateway = e.ai_gateway
        auto_capture = config.auto_capture_config if config else None
        gw_table = gateway.inference_table_config if gateway else None
        gw_tracking = gateway.usage_tracking_config if gateway else None
        entities = []
        for se in (config.served_entities if config else None) or []:
            entities.append(
                {
                    "entity_name": se.entity_name or se.name or "",
                    "workload_size": se.workload_size or "",
                    "workload_type": _workload_type(se),
                    "scale_to_zero": bool(se.scale_to_zero_enabled),
                    "is_external_or_fm": bool(se.external_model or se.foundation_model),
                }
            )
        out.append(
            {
                "name": e.name,
                "creator": e.creator or "",
                "task": e.task or "",
                "ready": e.state.ready.value if e.state and e.state.ready else "",
                "config_update": (
                    e.state.config_update.value if e.state and e.state.config_update else ""
                ),
                "created_ms": e.creation_timestamp or 0,
                "updated_ms": e.last_updated_timestamp or 0,
                "served_entities": entities,
                "is_external_or_fm": any(x["is_external_or_fm"] for x in entities),
                "has_inference_table": bool(
                    (auto_capture and auto_capture.enabled) or (gw_table and gw_table.enabled)
                ),
                "has_rate_limits": bool(gateway and gateway.rate_limits),
                "has_usage_tracking": bool(gw_tracking and gw_tracking.enabled),
            }
        )
    return out


def _workload_type(served_entity) -> str:
    wt = served_entity.workload_type
    return (wt.value if hasattr(wt, "value") else wt) or ""


def classify_serving_endpoints(
    endpoints: list[dict], now_ms: int, failed_grace_hours: int
) -> list[dict]:
    """Pure decision logic. One finding row per issue on an endpoint.

    - Endpoints stuck NOT_READY / UPDATE_FAILED past a grace period.
    - Small CPU workloads without scale-to-zero (GPU exempt: cold starts).
    - Endpoints without an inference table — no payload/audit trail.
    - External/foundation-model endpoints without AI Gateway rate limits
      or usage tracking.
    """
    findings = []

    def flag(e: dict, reason: str, action: str) -> None:
        findings.append(
            {"name": e["name"], "creator": e["creator"], "reason": reason, "action": action}
        )

    for e in endpoints:
        age_h = (now_ms - e["created_ms"]) / MS_PER_HOUR if e.get("created_ms") else 0
        stuck = e.get("ready") == "NOT_READY" or e.get("config_update") == "UPDATE_FAILED"
        if stuck and age_h >= failed_grace_hours:
            state = e.get("config_update") if e.get("config_update") == "UPDATE_FAILED" \
                else e.get("ready")
            flag(e, f"endpoint {state} for over {failed_grace_hours}h", "review-failed-endpoint")
        for se in e.get("served_entities", []):
            is_cpu = se.get("workload_type", "") in ("", "CPU")
            if is_cpu and se.get("workload_size") == "Small" and not se.get("scale_to_zero"):
                flag(
                    e,
                    f"served entity '{se['entity_name']}' is Small/CPU without scale-to-zero",
                    "enable-scale-to-zero (manual)",
                )
        if not e.get("has_inference_table"):
            flag(e, "no inference table — requests are not captured for audit/monitoring",
                 "enable-inference-table")
        if e.get("is_external_or_fm"):
            if not e.get("has_rate_limits"):
                flag(e, "external/foundation model without AI Gateway rate limits",
                     "add-ai-gateway-rate-limits")
            if not e.get("has_usage_tracking"):
                flag(e, "external/foundation model without AI Gateway usage tracking",
                     "enable-usage-tracking")
    return findings


def find_stale_endpoints(
    endpoints: list[dict], usage_rows: list[dict], now_ms: int, stale_days: int
) -> list[dict]:
    """Pure decision logic: endpoints with zero requests in the usage window.

    ``usage_rows`` comes from the endpoint_token_usage query; an endpoint
    younger than ``stale_days`` is never flagged (it has not had a full window).
    """
    active = {r.get("endpoint_name") for r in usage_rows}
    stale = []
    for e in endpoints:
        age_days = (now_ms - e["created_ms"]) / MS_PER_DAY if e.get("created_ms") else 0
        if e["name"] not in active and age_days >= stale_days:
            stale.append(
                {
                    "name": e["name"],
                    "creator": e["creator"],
                    "reason": f"no requests in the last {stale_days}d",
                    "action": "review-or-delete (manual)",
                }
            )
    return stale


# --- serving & AI/ML spend --------------------------------------------------

def serving_cost(w: WorkspaceClient, warehouse_id: str, days: int) -> list[dict]:
    """AI/ML spend by product, SKU and serving endpoint over the last N days."""
    return run_query(w, load_query("serving_cost"), warehouse_id, {"days": days})


def endpoint_token_usage(w: WorkspaceClient, warehouse_id: str, days: int) -> list[dict]:
    """Token usage per endpoint and requester over the last N days."""
    return run_query(w, load_query("endpoint_token_usage"), warehouse_id, {"days": days})

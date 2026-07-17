"""Cost & usage monitoring built on system.billing tables, plus compute
right-sizing checks (cluster/warehouse utilization, failed-run waste).

Utilization checks split SQL fetch from pure classification so the decision
logic stays unit-testable offline. Statement Execution returns every value as
a string, so the pure functions coerce numerics defensively.
"""

from __future__ import annotations

from databricks.sdk import WorkspaceClient

from dbx_platform.system_tables import load_query, run_query


def usage_report(w: WorkspaceClient, warehouse_id: str, days: int) -> list[dict]:
    """DBU and list-price cost by SKU and workspace over the last N days."""
    return run_query(w, load_query("usage_last_30d"), warehouse_id, {"days": days})


def top_jobs(w: WorkspaceClient, warehouse_id: str, days: int, limit: int) -> list[dict]:
    """Most expensive jobs by list-price cost over the last N days."""
    return run_query(
        w, load_query("job_run_cost"), warehouse_id, {"days": days, "limit": limit}
    )


# --- right-sizing -------------------------------------------------------------

def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def cluster_utilization(w: WorkspaceClient, warehouse_id: str, days: int) -> list[dict]:
    """Per-cluster CPU/memory utilization with sizing metadata and spend."""
    return run_query(w, load_query("cluster_utilization"), warehouse_id, {"days": days})


def classify_cluster_utilization(
    rows: list[dict], cpu_threshold_pct: int, mem_threshold_pct: int
) -> list[dict]:
    """Pure decision logic: clusters whose observed load does not justify
    their size, ranked by what they cost.

    - p95 CPU and average memory both under their thresholds: downsize the
      node type or worker count.
    - Autoscale ranges whose observed peak never reached the configured max:
      lower the max (it only inflates the bill on a bad day).
    """
    findings = []
    for r in rows:
        p95_cpu = _num(r.get("p95_cpu_pct"), default=-1)
        avg_mem = _num(r.get("avg_mem_pct"), default=-1)
        cost = _num(r.get("list_cost_usd"))
        base = {
            "cluster_id": r.get("cluster_id"),
            "cluster_name": r.get("cluster_name") or "",
            "creator": r.get("creator") or "",
            "list_cost_usd": cost,
        }
        if 0 <= p95_cpu < cpu_threshold_pct and 0 <= avg_mem < mem_threshold_pct:
            findings.append(
                {
                    **base,
                    "reason": f"p95 CPU {p95_cpu:.0f}% and avg memory {avg_mem:.0f}% "
                              f"(thresholds {cpu_threshold_pct}%/{mem_threshold_pct}%)",
                    "action": "downsize-node-or-workers",
                }
            )
        max_autoscale = _num(r.get("max_autoscale_workers"))
        min_autoscale = _num(r.get("min_autoscale_workers"))
        observed = _num(r.get("max_observed_workers"), default=-1)
        if max_autoscale > min_autoscale and 0 <= observed < max_autoscale:
            findings.append(
                {
                    **base,
                    "reason": f"autoscale max {max_autoscale:.0f} never reached "
                              f"(observed peak {observed:.0f} workers)",
                    "action": "lower-autoscale-max",
                }
            )
    findings.sort(key=lambda f: f["list_cost_usd"], reverse=True)
    return findings


def failed_run_waste(
    w: WorkspaceClient, warehouse_id: str, days: int, limit: int
) -> list[dict]:
    """List cost burned on failed/timed-out job runs over the last N days."""
    return run_query(
        w, load_query("failed_run_cost"), warehouse_id, {"days": days, "limit": limit}
    )


def warehouse_utilization(w: WorkspaceClient, warehouse_id: str, days: int) -> list[dict]:
    """Per-warehouse spend vs query volume and queueing over the last N days."""
    return run_query(w, load_query("warehouse_utilization"), warehouse_id, {"days": days})


def classify_warehouse_utilization(
    rows: list[dict], min_queries: int, queue_warn_seconds: int
) -> list[dict]:
    """Pure decision logic: warehouses mis-sized in either direction.

    - Spend with zero or few queries: shorten auto-stop or shrink.
    - Sustained queueing at capacity: undersized for its load.
    """
    findings = []
    for r in rows:
        cost = _num(r.get("list_cost_usd"))
        queries = _num(r.get("query_count"))
        queue_s = _num(r.get("avg_queue_seconds"))
        base = {
            "warehouse_id": r.get("warehouse_id"),
            "list_cost_usd": cost,
            "query_count": int(queries),
        }
        if cost > 0 and queries == 0:
            findings.append(
                {**base, "reason": "billed with zero queries in the window",
                 "action": "reduce-auto-stop-or-delete"}
            )
        elif cost > 0 and queries < min_queries:
            findings.append(
                {**base,
                 "reason": f"only {queries:.0f} queries for ${cost:.2f} "
                           f"(threshold {min_queries})",
                 "action": "reduce-auto-stop-or-size"}
            )
        if queue_s >= queue_warn_seconds and queries > 0:
            findings.append(
                {**base,
                 "reason": f"avg {queue_s:.1f}s queueing at capacity "
                           f"(threshold {queue_warn_seconds}s)",
                 "action": "undersized-consider-scaling"}
            )
    findings.sort(key=lambda f: f["list_cost_usd"], reverse=True)
    return findings

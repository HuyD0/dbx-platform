"""Cost & usage monitoring built on system.billing tables."""

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

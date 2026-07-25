"""Run SQL against Unity Catalog system tables via the Statement Execution API.

Uses a SQL warehouse rather than a Spark session, so the same code runs from a
laptop and on serverless job compute (no Spark dependency in the wheel).
"""

from __future__ import annotations

import os
import time
from importlib import resources

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import (
    StatementParameterListItem,
    StatementState,
)

_UNAVAILABLE_MARKERS = (
    "TABLE_OR_VIEW_NOT_FOUND",
    "SCHEMA_NOT_FOUND",
    "CATALOG_NOT_FOUND",
    "PERMISSION_DENIED",
    "INSUFFICIENT_PERMISSIONS",
)

_POLL_INTERVAL_SECONDS = 2
# Batch/CLI budget: a scheduled aggregation may legitimately run for minutes.
_TIMEOUT_SECONDS = 300
# The interactive app sits behind the Databricks Apps HTTP gateway, which aborts
# a slow upstream request with an opaque 502 the browser can't interpret. When
# the dedicated warehouse is asleep (prod ships it stopped) the first query waits
# on a cold start well past that limit. `DBX_PLATFORM_STATEMENT_TIMEOUT_SECONDS`
# lets the app cap its own wait below the gateway timeout so it can return a
# typed 504 (rendered as "the warehouse query timed out — retry") instead.
_TIMEOUT_ENV_VAR = "DBX_PLATFORM_STATEMENT_TIMEOUT_SECONDS"


def _default_timeout_seconds() -> float:
    raw = os.environ.get(_TIMEOUT_ENV_VAR, "").strip()
    if not raw:
        return _TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return _TIMEOUT_SECONDS
    return value if value > 0 else _TIMEOUT_SECONDS


class SystemTablesUnavailableError(RuntimeError):
    """System tables are not enabled or not granted to the current principal."""


def load_query(name: str) -> str:
    """Load a packaged .sql file from dbx_platform/queries/."""
    return (resources.files("dbx_platform") / "queries" / f"{name}.sql").read_text()


def run_query(
    w: WorkspaceClient,
    sql: str,
    warehouse_id: str,
    parameters: dict[str, int | str] | None = None,
    row_limit: int = 5000,
    timeout_seconds: float | None = None,
) -> list[dict]:
    """Execute SQL on a warehouse and return rows as a list of dicts.

    ``timeout_seconds`` caps how long we wait for the statement to finish. It
    defaults to ``DBX_PLATFORM_STATEMENT_TIMEOUT_SECONDS`` (else 300s) so the
    interactive app can fail fast with a typed timeout instead of blocking until
    the fronting gateway returns an opaque 502.
    """
    if not warehouse_id:
        raise ValueError(
            "A SQL warehouse ID is required for system-table queries. "
            "Pass --warehouse-id, or set DBX_PLATFORM_WAREHOUSE_ID. "
            "List warehouses with: databricks warehouses list"
        )

    budget = timeout_seconds if timeout_seconds is not None else _default_timeout_seconds()

    params = None
    if parameters:
        params = [
            StatementParameterListItem(
                name=k,
                value=str(v),
                type="INT" if isinstance(v, int) else "STRING",
            )
            for k, v in parameters.items()
        ]

    # The server-side wait is capped at 50s by the API; never wait longer than
    # our own budget so a short interactive timeout still returns promptly.
    wait_seconds = max(5, min(30, int(budget))) if budget >= 5 else 0
    resp = w.statement_execution.execute_statement(
        statement=sql,
        warehouse_id=warehouse_id,
        parameters=params,
        row_limit=row_limit,
        wait_timeout=f"{wait_seconds}s",
    )

    deadline = time.monotonic() + budget
    while resp.status and resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
        if time.monotonic() > deadline:
            raise TimeoutError(f"Statement {resp.statement_id} still running after "
                               f"{budget:g}s; the warehouse may be starting — retry shortly.")
        time.sleep(_POLL_INTERVAL_SECONDS)
        resp = w.statement_execution.get_statement(resp.statement_id)

    if not resp.status or resp.status.state != StatementState.SUCCEEDED:
        message = resp.status.error.message if resp.status and resp.status.error else "unknown"
        if "system." in sql and any(m in message for m in _UNAVAILABLE_MARKERS):
            raise SystemTablesUnavailableError(
                f"Query against system tables failed: {message}\n"
                "System tables are likely not enabled for this metastore, or the running "
                "principal lacks USE SCHEMA + SELECT.\n"
                "Fix: enable schemas with 'databricks system-schemas enable <metastore-id> "
                "<schema>' (billing, access, lakeflow, compute) and grant e.g. "
                "'GRANT USE SCHEMA, SELECT ON SCHEMA system.lakeflow TO `<principal>`'. "
                "For scheduled jobs, <principal> is the job's run-as identity — in prod, "
                "the CI service principal. See docs/setup.md and docs/cloud-setup.md."
            )
        raise RuntimeError(f"Statement failed ({resp.status.state if resp.status else '?'}): "
                           f"{message}")

    columns = [c.name for c in resp.manifest.schema.columns] if resp.manifest else []
    rows: list[dict] = []
    result = resp.result
    while result is not None:
        for row in result.data_array or []:
            rows.append(dict(zip(columns, row, strict=False)))
        if result.next_chunk_index is not None:
            result = w.statement_execution.get_statement_result_chunk_n(
                resp.statement_id, result.next_chunk_index
            )
        else:
            result = None
    return rows

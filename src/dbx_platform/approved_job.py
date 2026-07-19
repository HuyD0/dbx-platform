"""Fail-closed validation for stateful Jobs launched by the action executor.

The action executor passes an immutable action ID and hash to the exact Job it
starts. This module additionally binds that approval to the current Databricks
Job ID and run ID by reading the durable action ledger. A copied action ID,
manual rerun, or direct local CLI call cannot unlock a stateful task.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from typing import Any

from databricks.sdk import WorkspaceClient

from dbx_platform.system_tables import run_query

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CLAIMED_STATUSES = {"EXECUTING", "VERIFYING", "SUCCEEDED"}
_AUTONOMOUS_TRIGGER = "PERIODIC"


class ApprovalGateError(RuntimeError):
    """The current run is not bound to one valid, approved action."""


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _table_prefix(catalog: str, schema: str) -> str:
    if not _IDENTIFIER.fullmatch(catalog) or not _IDENTIFIER.fullmatch(schema):
        raise ApprovalGateError("Unsafe action-ledger catalog or schema.")
    return f"`{catalog}`.`{schema}`"


def _validate_action_row(
    row: dict[str, Any],
    *,
    action_id: str,
    plan_hash: str,
    workspace_id: str,
    environment: str,
    job_id: int,
) -> None:
    try:
        plan = json.loads(str(row["plan_json"]))
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ApprovalGateError("The stored action plan is malformed.") from exc

    if str(row.get("action_id") or "") != action_id:
        raise ApprovalGateError("The stored action ID does not match this run.")
    if str(row.get("plan_hash") or "") != plan_hash:
        raise ApprovalGateError("The stored plan hash does not match this run.")
    if str(row.get("workspace_id") or "") != workspace_id:
        raise ApprovalGateError("The approved action belongs to another workspace.")
    if str(row.get("environment") or "") != environment:
        raise ApprovalGateError("The approved action belongs to another environment.")
    if str(row.get("action_type") or "") != "run-job":
        raise ApprovalGateError("The approved action is not a Job launch.")
    if str(row.get("status") or "") not in _CLAIMED_STATUSES:
        raise ApprovalGateError("The action executor has not claimed this plan.")
    if _canonical_hash(plan) != plan_hash:
        raise ApprovalGateError("The immutable action payload does not match its hash.")
    if (
        plan.get("action_id") != action_id
        or str(plan.get("workspace_id") or "") != workspace_id
        or str(plan.get("environment") or "") != environment
    ):
        raise ApprovalGateError("The indexed action differs from its immutable payload.")

    targets = plan.get("targets")
    if not isinstance(targets, list) or len(targets) != 1:
        raise ApprovalGateError("An approved Job launch must have exactly one target.")
    try:
        approved_job_id = int(targets[0]["job_id"])
        payload_job_id = int(plan["parameters"]["execution_payload"]["job_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ApprovalGateError("The approved Job target is malformed.") from exc
    if approved_job_id != job_id or payload_job_id != job_id:
        raise ApprovalGateError("This Job is not the exact approved target.")


def verify_approved_job_launch(
    w: WorkspaceClient,
    warehouse_id: str,
    *,
    catalog: str,
    schema: str,
    environment: str,
    action_id: str,
    plan_hash: str,
    job_id: int,
    run_id: int,
    wait_seconds: float = 30,
    query: Callable[..., list[dict]] = run_query,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    """Verify that the executor launched this exact stateful Job run.

    The ``VERIFYING`` action event contains the run ID returned by
    ``jobs.run_now``. Requiring that event makes a successful action single-use:
    manually rerunning the Job with copied parameters produces another run ID
    and fails before training, registration, or promotion.
    """

    if not action_id or not plan_hash or len(plan_hash) != 64:
        raise ApprovalGateError(
            "Stateful Jobs require an action ID and SHA-256 plan hash from "
            "Mission Control. Start manual runs with Automations > Jobs & "
            "schedules > Plan run; Databricks Run now and repair runs are "
            "intentionally rejected."
        )
    if job_id <= 0 or run_id <= 0:
        raise ApprovalGateError("Databricks did not provide a valid Job/run identity.")

    workspace_id = str(w.get_workspace_id())
    fq = _table_prefix(catalog, schema)
    parameters = {
        "workspace_id": workspace_id,
        "environment": environment,
        "action_id": action_id,
        "plan_hash": plan_hash,
    }
    rows = query(
        w,
        f"""
        SELECT workspace_id, environment, action_id, action_type, status,
               plan_json, plan_hash
        FROM {fq}.action_requests
        WHERE workspace_id = :workspace_id
          AND environment = :environment
          AND action_id = :action_id
          AND plan_hash = :plan_hash
        LIMIT 2
        """,
        warehouse_id,
        parameters=parameters,
        row_limit=2,
    )
    if len(rows) != 1:
        raise ApprovalGateError("Exactly one matching durable action is required.")
    _validate_action_row(
        rows[0],
        action_id=action_id,
        plan_hash=plan_hash,
        workspace_id=workspace_id,
        environment=environment,
        job_id=job_id,
    )

    approvals = query(
        w,
        f"""
        SELECT approval_id
        FROM {fq}.action_approvals
        WHERE workspace_id = :workspace_id
          AND environment = :environment
          AND action_id = :action_id
          AND plan_hash = :plan_hash
          AND decision = 'APPROVED'
        LIMIT 2
        """,
        warehouse_id,
        parameters=parameters,
        row_limit=2,
    )
    if len(approvals) != 1:
        raise ApprovalGateError("Exactly one matching human approval is required.")

    deadline = time.monotonic() + max(0, wait_seconds)
    event_parameters = {
        "workspace_id": workspace_id,
        "environment": environment,
        "action_id": action_id,
        "run_id": str(run_id),
    }
    while True:
        events = query(
            w,
            f"""
            SELECT event_id
            FROM {fq}.action_events
            WHERE workspace_id = :workspace_id
              AND environment = :environment
              AND action_id = :action_id
              AND to_status = 'VERIFYING'
              AND CAST(get_json_object(details_json, '$.result.run_id') AS STRING)
                  = :run_id
            LIMIT 2
            """,
            warehouse_id,
            parameters=event_parameters,
            row_limit=2,
        )
        if len(events) == 1:
            return
        if len(events) > 1:
            raise ApprovalGateError("The Job launch has duplicate executor events.")
        if time.monotonic() >= deadline:
            raise ApprovalGateError(
                "No executor event is bound to this exact Databricks run ID."
            )
        sleeper(1)


def verify_governed_write_launch(
    w: WorkspaceClient,
    warehouse_id: str,
    *,
    catalog: str,
    schema: str,
    environment: str,
    action_id: str,
    plan_hash: str,
    job_id: int,
    run_id: int,
    trigger_type: str,
    wait_seconds: float = 30,
    query: Callable[..., list[dict]] = run_query,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    """Allow an append-only write on its schedule or via an approved manual run.

    The Jobs API is authoritative for the exact Job/run/trigger tuple. Cron
    runs are autonomous evidence work. Every other trigger, including Run now,
    must also match the action executor's immutable run event.
    """

    if job_id <= 0 or run_id <= 0:
        raise ApprovalGateError(
            "Stateful commands run only inside an exact governed Databricks Job."
        )
    run = w.jobs.get_run(run_id)
    actual_run_id = int(getattr(run, "run_id", 0) or 0)
    actual_job_id = int(getattr(run, "job_id", 0) or 0)
    actual_trigger_raw = getattr(run, "trigger", "")
    actual_trigger = str(
        getattr(actual_trigger_raw, "value", actual_trigger_raw) or ""
    ).upper()
    claimed_trigger = str(trigger_type or "").upper()
    if actual_run_id != run_id or actual_job_id != job_id:
        raise ApprovalGateError("The current Job/run identity does not match Databricks.")
    if actual_trigger != claimed_trigger:
        raise ApprovalGateError("The claimed trigger type does not match Databricks.")
    if actual_trigger == _AUTONOMOUS_TRIGGER:
        return

    verify_approved_job_launch(
        w,
        warehouse_id,
        catalog=catalog,
        schema=schema,
        environment=environment,
        action_id=action_id,
        plan_hash=plan_hash,
        job_id=job_id,
        run_id=run_id,
        wait_seconds=wait_seconds,
        query=query,
        sleeper=sleeper,
    )

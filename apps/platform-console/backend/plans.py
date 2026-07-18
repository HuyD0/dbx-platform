"""In-memory stores backing the confirm-gate and background digest task.

A remediation plan is created by a dry-run, expires after 15 minutes, and is
single-use: it is removed from the store before its items are applied, so a
retry must re-plan against current workspace state. In-memory is a deliberate
fit for a single-instance Databricks App; losing plans on restart is safe
(the user just re-plans).
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

PLAN_TTL_SECONDS = 15 * 60


class PlanExpiredError(Exception):
    pass


class PlanNotFoundError(Exception):
    pass


class PlanStore:
    def __init__(self) -> None:
        self._plans: dict[str, dict] = {}
        self._lock = threading.Lock()

    def create(self, action: str, items: list[dict], payload: Any, summary: dict) -> dict:
        plan_id = str(uuid.uuid4())
        now = time.time()
        plan = {
            "plan_id": plan_id,
            "action": action,
            "created_at": now,
            "expires_at": now + PLAN_TTL_SECONDS,
            "items": items,
            "payload": payload,
            "summary": summary,
            "confirm_phrase": f"apply {action} {len(items)}",
        }
        with self._lock:
            self._plans[plan_id] = plan
        return plan

    def take(self, action: str, plan_id: str) -> dict:
        """Pop the plan (single-use). Raises if unknown, mismatched or expired."""
        with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None or plan["action"] != action:
                raise PlanNotFoundError(plan_id)
            del self._plans[plan_id]
        if time.time() > plan["expires_at"]:
            raise PlanExpiredError(plan_id)
        return plan


class TaskStore:
    """Background one-shot tasks (digest generation) with polled status."""

    def __init__(self) -> None:
        self._tasks: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start(self, target, *args) -> str:
        task_id = str(uuid.uuid4())
        with self._lock:
            self._tasks[task_id] = {"state": "running"}

        def _run() -> None:
            try:
                result = target(*args)
                self._finish(task_id, {"state": "done", **result})
            except Exception as e:  # noqa: BLE001 — status must always resolve
                self._finish(task_id, {"state": "failed", "error": str(e)})

        threading.Thread(target=_run, daemon=True).start()
        return task_id

    def _finish(self, task_id: str, status: dict) -> None:
        with self._lock:
            self._tasks[task_id] = status

    def get(self, task_id: str) -> dict | None:
        with self._lock:
            return self._tasks.get(task_id)

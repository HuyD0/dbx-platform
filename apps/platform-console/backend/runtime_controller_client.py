"""Submit exact Hibernate/Wake work to the out-of-band controller Job."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from backend.control_plane import (
    ActionRequest,
    ActionStatus,
    ControlPlaneError,
    PlanIntegrityError,
)

_PLAN_OPERATION = {
    "runtime.hibernate": "plan-hibernate",
    "runtime.wake": "plan-wake",
}
_EXECUTE_OPERATION = {
    "runtime.hibernate": "execute-hibernate",
    "runtime.wake": "execute-wake",
}


class RuntimeControllerError(ControlPlaneError):
    code = "runtime_controller_failed"


def extract_review_output(logs: str) -> dict[str, Any]:
    """Find the controller's final JSON review artifact in task stdout."""
    decoder = json.JSONDecoder()
    matches: list[dict[str, Any]] = []
    for index, character in enumerate(logs):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(logs[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and {
            "action_id",
            "action_type",
            "plan_hash",
        }.issubset(value):
            matches.append(value)
    if not matches:
        raise RuntimeControllerError(
            "The power-controller completed without a JSON review artifact."
        )
    return matches[-1]


class RuntimeControllerClient:
    def __init__(
        self,
        workspace_client,
        job_id: int,
        repository,
    ) -> None:
        if job_id <= 0:
            raise ValueError("The power-controller job ID must be positive.")
        self.workspace_client = workspace_client
        self.job_id = job_id
        self.repository = repository

    def submit_plan(self, action_type: str) -> ActionRequest:
        operation = _PLAN_OPERATION.get(action_type)
        if operation is None:
            raise ValueError(f"Unsupported runtime plan action {action_type!r}.")
        waiter = self.workspace_client.jobs.run_now(
            job_id=self.job_id,
            job_parameters={
                "operation": operation,
                "plan_id": "",
                "plan_hash": "",
                "confirmation": "",
            },
        )
        run = waiter.result(timeout=timedelta(minutes=15))
        task_run_id = self._task_run_id(run, int(waiter.run_id))
        output = self.workspace_client.jobs.get_run_output(task_run_id)
        if output.error:
            raise RuntimeControllerError(
                "The power-controller plan task failed before producing a review artifact."
            )
        review = extract_review_output(output.logs or "")
        action_id = str(review["action_id"])
        action = self.repository.get_action(action_id)
        if action is None:
            raise RuntimeControllerError(
                "The power-controller review was not durably persisted."
            )
        action.assert_integrity()
        if action.action_type != action_type:
            raise PlanIntegrityError(
                "The controller returned a different runtime action type."
            )
        if action.plan_hash != str(review["plan_hash"]):
            raise PlanIntegrityError(
                "The controller output hash differs from the durable action."
            )
        if action.status != ActionStatus.AWAITING_APPROVAL:
            raise RuntimeControllerError(
                f"New controller plan is unexpectedly {action.status.value}."
            )
        return action

    def submit_execute(self, action: ActionRequest) -> int:
        operation = _EXECUTE_OPERATION.get(action.action_type)
        if operation is None:
            raise ValueError(f"Unsupported runtime execute action {action.action_type!r}.")
        if action.status != ActionStatus.APPROVED:
            raise PlanIntegrityError("Only an approved runtime action can be submitted.")
        waiter = self.workspace_client.jobs.run_now(
            job_id=self.job_id,
            idempotency_token=action.idempotency_key,
            job_parameters={
                "operation": operation,
                "plan_id": action.action_id,
                "plan_hash": action.plan_hash,
                # Approval is loaded from action_approvals by the controller.
                "confirmation": "",
            },
        )
        return int(waiter.run_id)

    @staticmethod
    def _task_run_id(run, parent_run_id: int) -> int:
        tasks = list(run.tasks or [])
        for task in tasks:
            if task.task_key == "runtime_control" and task.run_id is not None:
                return int(task.run_id)
        if len(tasks) == 1 and tasks[0].run_id is not None:
            return int(tasks[0].run_id)
        return parent_run_id

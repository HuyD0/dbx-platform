"""Submit an approved non-runtime action ID to the dedicated executor Job."""

from __future__ import annotations

from backend.control_plane import ActionRequest, ActionStatus, PlanIntegrityError

_EXECUTOR_ACTIONS = frozenset(
    {
        "stale-clusters",
        "orphaned-jobs",
        "token-revoke",
        "policy-sync",
        "run-job",
        "configure-budget",
    }
)


class ActionExecutorClient:
    def __init__(self, workspace_client, job_id: int) -> None:
        if job_id <= 0:
            raise ValueError("The action-executor job ID must be positive.")
        self.workspace_client = workspace_client
        self.job_id = job_id

    def submit(self, action: ActionRequest) -> int:
        if action.status != ActionStatus.APPROVED:
            raise PlanIntegrityError("Only an approved action can be submitted.")
        if action.action_type not in _EXECUTOR_ACTIONS:
            raise PlanIntegrityError(
                f"Action type {action.action_type!r} is not executor-allowlisted."
            )
        waiter = self.workspace_client.jobs.run_now(
            job_id=self.job_id,
            idempotency_token=action.idempotency_key,
            job_parameters={"action_id": action.action_id},
        )
        return int(waiter.run_id)

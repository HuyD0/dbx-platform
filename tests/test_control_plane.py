"""Security and lifecycle tests for the Mission Control action foundation."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from starlette.requests import Request

APP_DIR = Path(__file__).resolve().parent.parent / "apps" / "platform-console"
sys.path.insert(0, str(APP_DIR))

from backend import cache, deps  # noqa: E402
from backend import identity as identity_module  # noqa: E402
from backend.action_executor_client import ActionExecutorClient  # noqa: E402
from backend.control_plane import (  # noqa: E402
    ActionConflictError,
    ActionExpiredError,
    ActionRequest,
    ActionService,
    ActionStatus,
    Actor,
    ExecutionUnavailableError,
    Finding,
    PlanIntegrityError,
    PlanSpec,
    PreconditionsChangedError,
    RiskLevel,
    canonical_json,
    sha256_json,
)
from backend.control_plane_repository import (  # noqa: E402
    InMemoryControlPlaneRepository,
    SQLControlPlaneRepository,
)
from backend.identity import (  # noqa: E402
    IdentityVerifier,
    UnauthenticatedError,
    UnauthorizedError,
    mask_for_viewer,
)
from backend.routers import actions, control_plane  # noqa: E402
from backend.runtime_controller_client import (  # noqa: E402
    RuntimeControllerClient,
    extract_review_output,
)


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 17, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


def _actor(*roles: str) -> Actor:
    return Actor(
        actor_id="user-1",
        email="operator@example.com",
        roles=frozenset(roles),
    )


def _spec(state: list[dict] | None = None) -> PlanSpec:
    items = state or [{"cluster_id": "c-1", "action": "terminate"}]
    return PlanSpec(
        action_type="stale-clusters",
        targets=items,
        parameters={"execution_payload": items},
        preconditions={"state_sha256": sha256_json(items)},
        before_state=items,
        after_state={"state": "TERMINATED"},
        impact={"target_count": len(items)},
        rollback={"supported": True},
        verification={"strategy": "re-read"},
        risk=RiskLevel.MEDIUM,
    )


def _service(clock: Clock | None = None):
    repository = InMemoryControlPlaneRepository()
    clock = clock or Clock()
    return (
        ActionService(
            repository,
            workspace_id="123",
            environment="dev",
            now=clock,
        ),
        repository,
        clock,
    )


def _same_state(action) -> dict:
    return {"state_sha256": action.preconditions["state_sha256"]}


def test_plan_hash_detects_persisted_payload_tampering():
    service, repository, _ = _service()
    planned = service.plan(_spec(), _actor("proposer"))
    # Simulate corruption or a direct SQL edit after planning.
    repository._actions[planned.action_id].targets[0]["cluster_id"] = "c-evil"

    with pytest.raises(PlanIntegrityError):
        service.approve(
            planned.action_id,
            actor=_actor("approver"),
            plan_hash=planned.plan_hash,
            confirmation=planned.confirm_phrase,
            revalidate=_same_state,
        )
    assert repository.list_approvals(planned.action_id) == []


def test_approval_rejects_client_hash_tampering():
    service, repository, _ = _service()
    planned = service.plan(_spec(), _actor("proposer"))

    with pytest.raises(PlanIntegrityError):
        service.approve(
            planned.action_id,
            actor=_actor("approver"),
            plan_hash="0" * 64,
            confirmation=planned.confirm_phrase,
            revalidate=_same_state,
        )
    assert repository.get_action(planned.action_id).status == ActionStatus.AWAITING_APPROVAL


def test_expired_plan_transitions_and_cannot_be_approved():
    service, repository, clock = _service()
    planned = service.plan(_spec(), _actor("proposer"))
    clock.value += timedelta(minutes=16)

    with pytest.raises(ActionExpiredError):
        service.approve(
            planned.action_id,
            actor=_actor("approver"),
            plan_hash=planned.plan_hash,
            confirmation=planned.confirm_phrase,
            revalidate=_same_state,
        )
    assert repository.get_action(planned.action_id).status == ActionStatus.EXPIRED
    assert repository.list_approvals(planned.action_id) == []


def test_approval_is_single_use_and_audited():
    service, repository, _ = _service()
    planned = service.plan(_spec(), _actor("proposer"))
    approved = service.approve(
        planned.action_id,
        actor=_actor("approver"),
        plan_hash=planned.plan_hash,
        confirmation=planned.confirm_phrase,
        revalidate=_same_state,
    )
    assert approved.status == ActionStatus.APPROVED

    with pytest.raises(ActionConflictError):
        service.approve(
            planned.action_id,
            actor=_actor("approver"),
            plan_hash=planned.plan_hash,
            confirmation=planned.confirm_phrase,
            revalidate=_same_state,
        )
    assert len(repository.list_approvals(planned.action_id)) == 1
    assert [event.event_type for event in repository.list_events(planned.action_id)] == [
        "PLAN_CREATED",
        "STATUS_APPROVED",
    ]


def test_non_approver_cannot_approve():
    service, repository, _ = _service()
    planned = service.plan(_spec(), _actor("proposer"))

    with pytest.raises(ActionConflictError):
        service.approve(
            planned.action_id,
            actor=_actor("proposer"),
            plan_hash=planned.plan_hash,
            confirmation=planned.confirm_phrase,
            revalidate=_same_state,
        )
    assert repository.get_action(planned.action_id).status == ActionStatus.AWAITING_APPROVAL


def test_precondition_drift_marks_plan_stale():
    service, repository, _ = _service()
    planned = service.plan(_spec(), _actor("proposer"))

    with pytest.raises(PreconditionsChangedError, match="changed after planning"):
        service.approve(
            planned.action_id,
            actor=_actor("approver"),
            plan_hash=planned.plan_hash,
            confirmation=planned.confirm_phrase,
            revalidate=lambda _: {"state_sha256": sha256_json([{"different": True}])},
        )
    assert repository.get_action(planned.action_id).status == ActionStatus.STALE


def test_memory_repository_is_proposal_only_for_execution():
    service, _, _ = _service()
    planned = service.plan(_spec(), _actor("proposer"))
    service.approve(
        planned.action_id,
        actor=_actor("approver"),
        plan_hash=planned.plan_hash,
        confirmation=planned.confirm_phrase,
        revalidate=_same_state,
    )

    with pytest.raises(ExecutionUnavailableError):
        service.claim_for_execution(
            planned.action_id,
            plan_hash=planned.plan_hash,
            executor=_actor("executor"),
            revalidate=_same_state,
        )


def test_legacy_finding_is_normalized_and_rankable():
    repository = InMemoryControlPlaneRepository()
    repository.add_finding(
        {
            "run_ts": "2026-07-17T12:00:00+00:00",
            "area": "security",
            "check_name": "stale-pat",
            "resource": "token-7",
            "reason": "older than policy",
            "action": "token-revoke",
            "details": '{"age_days": 120}',
        }
    )
    finding = Finding.model_validate(repository.list_findings()[0])
    assert finding.pillar == "SECURITY"
    assert finding.severity == "MEDIUM"
    assert finding.affected_resources == [{"resource_id": "token-7"}]
    assert finding.evidence == {"age_days": 120}
    assert len(finding.finding_id) == 64


def test_sql_row_payload_tampering_is_detected():
    service, _, _ = _service()
    action = service.plan(_spec(), _actor("proposer"))
    document = action.immutable_document()
    document["targets"][0]["cluster_id"] = "tampered"
    row = {
        **{
            key: getattr(action, key)
            for key in (
                "action_id",
                "action_type",
                "workspace_id",
                "environment",
                "confirm_phrase",
                "idempotency_key",
            )
        },
        "plan_json": canonical_json(document),
        "plan_hash": action.plan_hash,
        "status": action.status.value,
        "updated_at": action.updated_at,
        "terminal_reason": None,
    }
    with pytest.raises(PlanIntegrityError):
        SQLControlPlaneRepository._action_from_row(row)


def test_repository_hides_cross_environment_action_ids():
    repository = InMemoryControlPlaneRepository("workspace-1", "dev")
    foreign = ActionRequest.create(
        action_type="stale-clusters",
        workspace_id="workspace-1",
        environment="prod",
        targets=[],
        parameters={},
        preconditions={"state_sha256": sha256_json([])},
        before_state=[],
        after_state=[],
        impact={},
        rollback={},
        verification={},
        risk=RiskLevel.MEDIUM,
        proposer=_actor("proposer"),
    )
    # Simulate an action inserted by the prod-scoped repository sharing the
    # same backing table.
    repository._actions[foreign.action_id] = foreign
    assert repository.get_action(foreign.action_id) is None
    assert repository.list_actions() == []


def test_sql_get_and_list_queries_include_scope_predicates():
    repository = SQLControlPlaneRepository(
        MagicMock(),
        "warehouse-1",
        "main",
        "dbx_platform",
        workspace_id="workspace-1",
        environment="dev",
    )
    repository._initialized = True
    calls = []

    def record(sql, parameters=None):
        calls.append((sql, parameters))
        return []

    repository._run = record
    assert repository.get_action("action-1") is None
    assert repository.list_actions() == []
    for sql, parameters in calls:
        assert "workspace_id = :scope_workspace_id" in sql
        assert "environment = :scope_environment" in sql
        assert parameters["scope_workspace_id"] == "workspace-1"
        assert parameters["scope_environment"] == "dev"


def test_sql_approval_event_and_finding_reads_include_scope_predicates(
    monkeypatch,
):
    repository = SQLControlPlaneRepository(
        MagicMock(),
        "warehouse-1",
        "main",
        "dbx_platform",
        workspace_id="workspace-1",
        environment="dev",
    )
    repository._initialized = True
    action = ActionRequest.create(
        action_type="stale-clusters",
        workspace_id="workspace-1",
        environment="dev",
        targets=[],
        parameters={},
        preconditions={"state_sha256": sha256_json([])},
        before_state=[],
        after_state=[],
        impact={},
        rollback={},
        verification={},
        risk=RiskLevel.MEDIUM,
        proposer=_actor("proposer"),
    )
    monkeypatch.setattr(repository, "get_action", lambda _: action)
    calls = []

    def record(sql, parameters=None):
        calls.append((sql, parameters or {}))
        if sql.startswith("DESCRIBE TABLE"):
            return [
                {"col_name": name}
                for name in (
                    "finding_id",
                    "workspace_id",
                    "environment",
                )
            ]
        return []

    repository._run = record
    assert repository.list_approvals(action.action_id) == []
    assert repository.list_events(action.action_id) == []
    assert repository.list_findings() == []
    scoped_queries = [
        (sql, parameters)
        for sql, parameters in calls
        if sql.startswith("SELECT")
    ]
    assert len(scoped_queries) == 3
    for sql, parameters in scoped_queries:
        assert "workspace_id = :scope_workspace_id" in sql
        assert "environment = :scope_environment" in sql
        assert parameters["scope_workspace_id"] == "workspace-1"
        assert parameters["scope_environment"] == "dev"


def test_sql_migration_owns_control_plane_not_llm_telemetry():
    repository = SQLControlPlaneRepository(
        MagicMock(),
        "warehouse-1",
        "main",
        "dbx_platform",
        auto_migrate=True,
    )
    statements: list[str] = []

    def record(sql, parameters=None):
        statements.append(sql)
        if sql.startswith("DESCRIBE TABLE"):
            base = [
                "run_ts",
                "area",
                "check_name",
                "resource",
                "reason",
                "action",
                "details",
            ]
            canonical = [
                "finding_id",
                "workspace_id",
                "environment",
                "pillar",
                "severity",
                "likelihood",
                "financial_impact_usd",
                "slo_impact",
                "confidence",
                "owner",
                "affected_resources_json",
                "evidence_json",
                "freshness_at",
                "first_seen_at",
                "last_seen_at",
                "state",
                "proposed_action_type",
                "blast_radius",
            ]
            return [{"col_name": name} for name in base + canonical]
        return []

    repository._run = record
    repository.migrate()
    sql = "\n".join(statements)
    assert "action_requests" in sql
    assert "managed_resources" in sql
    assert "platform_runtime_state" in sql
    assert "workspace_id STRING NOT NULL" in sql
    assert "environment STRING NOT NULL" in sql
    assert "llm_usage_hourly" not in sql
    assert "llm_cost_daily" not in sql


def _runtime_action(repository) -> ActionRequest:
    action = ActionRequest.create(
        action_type="runtime.hibernate",
        workspace_id="local",
        environment="dev",
        targets=[
            {
                "resource_key": "platform_console",
                "resource_type": "APP",
                "resource_id": "platform-console",
            }
        ],
        parameters={"inventory_hash": "inventory"},
        preconditions={
            "resources": {
                "platform_console": {
                    "resource_id": "platform-console",
                    "state": "RUNNING",
                    "config_hash": "before",
                }
            }
        },
        before_state={"resources": {"platform_console": {"state": "RUNNING"}}},
        after_state={"resources": {"platform_console": {"state": "STOPPED"}}},
        impact={"target_count": 1},
        rollback={"strategy": "restore-exact-before-state"},
        verification={"resource_states": {"platform_console": "STOPPED"}},
        risk=RiskLevel.MEDIUM,
        proposer=_actor("automation-proposer"),
        now=datetime.now(UTC),
    )
    repository.create_action(action)
    return action


def test_runtime_controller_client_resolves_durable_review_and_submits_exact_hash():
    repository = InMemoryControlPlaneRepository()
    action = _runtime_action(repository)
    workspace = MagicMock()
    waiter = MagicMock()
    waiter.run_id = 101
    task = MagicMock()
    task.task_key = "runtime_control"
    task.run_id = 102
    completed = MagicMock()
    completed.tasks = [task]
    waiter.result.return_value = completed
    workspace.jobs.run_now.return_value = waiter
    output = MagicMock()
    output.error = None
    output.logs = (
        "controller startup\n"
        + canonical_json(
            {
                "action_id": action.action_id,
                "action_type": action.action_type,
                "plan_hash": action.plan_hash,
                "plan": action.immutable_document(),
            }
        )
        + "\nfinished"
    )
    workspace.jobs.get_run_output.return_value = output
    client = RuntimeControllerClient(workspace, 44, repository)

    resolved = client.submit_plan("runtime.hibernate")
    assert resolved.action_id == action.action_id
    workspace.jobs.get_run_output.assert_called_once_with(102)
    plan_call = workspace.jobs.run_now.call_args.kwargs
    assert plan_call["job_id"] == 44
    assert plan_call["job_parameters"]["operation"] == "plan-hibernate"

    approved = repository.transition(
        action.action_id,
        expected={ActionStatus.AWAITING_APPROVAL},
        target=ActionStatus.APPROVED,
        actor_id="user-1",
    )
    run_id = client.submit_execute(approved)
    assert run_id == 101
    execute_call = workspace.jobs.run_now.call_args.kwargs
    assert execute_call["idempotency_token"] == action.idempotency_key
    assert execute_call["job_parameters"] == {
        "operation": "execute-hibernate",
        "plan_id": action.action_id,
        "plan_hash": action.plan_hash,
        "confirmation": "",
    }


@pytest.mark.parametrize("action_type", ["stale-clusters", "configure-budget"])
def test_action_executor_submission_contains_only_approved_action_id(action_type):
    repository = InMemoryControlPlaneRepository()
    service = ActionService(
        repository,
        workspace_id="123",
        environment="dev",
        now=Clock(),
    )
    action = service.plan(
        _spec().model_copy(update={"action_type": action_type}),
        _actor("proposer"),
    )
    approved = service.approve(
        action.action_id,
        actor=_actor("approver"),
        plan_hash=action.plan_hash,
        confirmation=action.confirm_phrase,
        revalidate=_same_state,
    )
    workspace = MagicMock()
    waiter = MagicMock()
    waiter.run_id = 501
    workspace.jobs.run_now.return_value = waiter

    run_id = ActionExecutorClient(workspace, 77).submit(approved)
    assert run_id == 501
    workspace.jobs.run_now.assert_called_once_with(
        job_id=77,
        idempotency_token=approved.idempotency_key,
        job_parameters={"action_id": approved.action_id},
    )


def test_review_log_parser_ignores_unrelated_json():
    review = extract_review_output(
        'prefix {"message":"noise"}\n'
        '{"action_id":"a","action_type":"runtime.wake","plan_hash":"'
        + ("1" * 64)
        + '"}\n'
    )
    assert review["action_type"] == "runtime.wake"


def _request(headers: dict[str, str]) -> Request:
    raw = [(key.lower().encode(), value.encode()) for key, value in headers.items()]
    return Request({"type": "http", "headers": raw})


def test_app_workspace_client_forces_oauth_m2m(monkeypatch):
    deps.get_ws.cache_clear()
    monkeypatch.setenv("DATABRICKS_APP_NAME", "platform-console")
    workspace = MagicMock()
    constructor = MagicMock(return_value=workspace)
    monkeypatch.setattr("databricks.sdk.WorkspaceClient", constructor)

    assert deps.get_ws() is workspace
    constructor.assert_called_once_with(
        auth_type="oauth-m2m",
        scopes=["all-apis"],
    )
    deps.get_ws.cache_clear()


def test_non_app_workspace_client_uses_unified_auth(monkeypatch):
    deps.get_ws.cache_clear()
    monkeypatch.delenv("DATABRICKS_APP_NAME", raising=False)
    workspace = MagicMock()
    factory = MagicMock(return_value=workspace)
    monkeypatch.setattr(deps, "get_client", factory)

    assert deps.get_ws() is workspace
    factory.assert_called_once_with(None)
    deps.get_ws.cache_clear()


def test_forwarded_email_alone_is_never_an_identity():
    verifier = IdentityVerifier(lambda: MagicMock())
    with pytest.raises(UnauthenticatedError):
        verifier.verify(_request({"X-Forwarded-Email": "admin@example.com"}))


def test_token_identity_and_group_are_verified_server_side():
    workspace = MagicMock()
    workspace.config.host = "https://workspace.example"
    user_workspace = MagicMock()
    user_workspace.api_client.do.return_value = {
        "id": "user-1",
        "userName": "operator@example.com",
        "groups": [{"display": "dbx-platform-approvers"}],
    }
    constructed = []
    verifier = IdentityVerifier(
        lambda: workspace,
        user_workspace_client_factory=lambda host, token: (
            constructed.append((host, token)) or user_workspace
        ),
    )
    actor = verifier.verify(
        _request(
            {
                "X-Forwarded-Access-Token": "opaque",
                # This is the IdP user identifier, not the workspace SCIM ID.
                "X-Forwarded-User": "idp-subject-845",
                "X-Forwarded-Email": "spoofed@example.com",
            }
        ),
        require_approver=True,
    )
    assert actor.actor_id == "user-1"
    assert actor.email == "operator@example.com"
    assert actor.has_role("approver")
    assert actor.has_role("proposer")
    assert constructed == [("https://workspace.example", "opaque")]
    workspace.api_client.do.assert_not_called()


def test_default_user_client_isolated_from_app_oauth_credentials(monkeypatch):
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "app-service-principal")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "app-secret")
    workspace = MagicMock()
    workspace.config.host = "https://workspace.example"
    user_workspace = MagicMock()
    user_workspace.api_client.do.return_value = {
        "id": "user-1",
        "userName": "operator@example.com",
        "groups": [],
    }
    constructor = MagicMock(return_value=user_workspace)
    monkeypatch.setattr(identity_module, "WorkspaceClient", constructor)

    actor = IdentityVerifier(lambda: workspace).verify(
        _request({"X-Forwarded-Access-Token": "opaque-user-token"})
    )

    assert actor.actor_id == "user-1"
    constructor.assert_called_once_with(
        host="https://workspace.example",
        token="opaque-user-token",
        auth_type="pat",
    )


def test_verified_viewer_cannot_propose_without_operator_membership():
    workspace = MagicMock()
    workspace.config.host = "https://workspace.example"
    user_workspace = MagicMock()
    user_workspace.api_client.do.return_value = {
        "id": "viewer-1",
        "userName": "viewer@example.com",
        "groups": [],
    }
    verifier = IdentityVerifier(
        lambda: workspace,
        user_workspace_client_factory=lambda _host, _token: user_workspace,
    )
    request = _request({"X-Forwarded-Access-Token": "opaque"})
    viewer = verifier.verify(request)
    assert viewer.has_role("viewer")
    assert not viewer.has_role("proposer")
    with pytest.raises(UnauthorizedError):
        verifier.verify(request, require_proposer=True)


def test_user_repository_uses_app_client_after_actor_verification(monkeypatch):
    monkeypatch.setenv("DATABRICKS_APP_NAME", "platform-console")
    monkeypatch.setenv("DATABRICKS_WORKSPACE_ID", "123")
    monkeypatch.setenv("DBX_PLATFORM_ENVIRONMENT", "prod")
    workspace = MagicMock()
    workspace.config.host = "https://workspace.example"
    monkeypatch.setattr(deps, "get_ws", lambda: workspace)
    monkeypatch.setattr(
        deps,
        "get_settings",
        lambda: deps.Settings(warehouse_id="warehouse-1"),
    )
    request = _request({"X-Forwarded-Access-Token": "opaque-user-token"})
    request.state.actor = Actor(
        actor_id="operator-1",
        email="operator@example.com",
        roles=frozenset({"proposer"}),
    )
    repository = deps.get_user_control_plane_repository(request)

    assert repository.workspace_client is workspace


def test_user_repository_rejects_unverified_request(monkeypatch):
    monkeypatch.setenv("DATABRICKS_APP_NAME", "platform-console")
    with pytest.raises(RuntimeError, match="verified Databricks App user"):
        deps.get_user_control_plane_repository(_request({}))


def test_viewer_response_masks_proposer_approver_and_owner_identity():
    viewer = Actor(
        actor_id="viewer-1",
        email="viewer@example.com",
        roles=frozenset({"viewer"}),
    )
    masked = mask_for_viewer(
        {
            "proposer_id": "operator-1",
            "proposer_email": "operator@example.com",
            "owner": "owner@example.com",
            "approvals": [
                {
                    "approver_id": "approver-1",
                    "approver_email": "approver@example.com",
                    "plan_hash": "safe-to-show",
                }
            ],
        },
        viewer,
    )
    assert masked["proposer_id"] == "[redacted]"
    assert masked["owner"] == "[redacted]"
    assert masked["approvals"][0]["approver_email"] == "[redacted]"
    assert masked["approvals"][0]["plan_hash"] == "safe-to-show"


@pytest.fixture()
def control_plane_client(monkeypatch):
    monkeypatch.setenv("DBX_PLATFORM_CONTROL_PLANE_REPOSITORY", "memory")
    monkeypatch.setenv("DBX_PLATFORM_LOCAL_IDENTITY", "true")
    monkeypatch.setenv("DBX_PLATFORM_LOCAL_ACTOR_ID", "user-1")
    monkeypatch.setenv("DBX_PLATFORM_LOCAL_ROLES", "approver")
    monkeypatch.setenv("DBX_PLATFORM_CONSOLE_ACTIONS", "true")
    deps.get_control_plane_repository.cache_clear()
    deps.get_identity_verifier.cache_clear()
    cache.clear()
    workspace = MagicMock()
    job = MagicMock()
    job.job_id = 7
    job.settings.name = "[dbx-platform] cost-usage-report"
    job.run_as_user_name = "platform-runner@example.com"
    job.settings.as_dict.side_effect = lambda: {
        "name": job.settings.name,
        "run_as": {"user_name": job.run_as_user_name},
        "tasks": [
            {
                "task_key": "report",
                "notebook_task": {"notebook_path": "/Workspace/platform/report"},
            }
        ],
    }
    workspace.jobs.list.return_value = [job]
    workspace.jobs.get.return_value = job
    monkeypatch.setattr(deps, "get_ws", lambda: workspace)
    action_executor = MagicMock()
    action_executor.submit.return_value = 8001
    monkeypatch.setattr(
        deps,
        "get_action_executor_client",
        lambda: action_executor,
    )
    deps.get_control_plane_repository().add_managed_resource(
        {
            "workspace_id": "local",
            "environment": "dev",
            "resource_id": "7",
            "resource_type": "JOB",
            "ownership": "BUNDLE",
            "protected": False,
        }
    )
    items = [{"cluster_id": "c-1", "action": "terminate"}]
    monkeypatch.setitem(
        actions.REGISTRY,
        "stale-clusters",
        {
            "plan": lambda: (items, items, {"terminate": 1}),
            "apply": MagicMock(),
        },
    )
    from backend.app import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        client.app.state.test_job = job
        yield client
    deps.get_control_plane_repository.cache_clear()
    deps.get_identity_verifier.cache_clear()


def test_generic_api_hash_auth_replay_and_audit(control_plane_client):
    planned = control_plane_client.post(
        "/api/action-requests/plan",
        json={"action_type": "stale-clusters"},
    )
    assert planned.status_code == 200
    plan = planned.json()
    assert len(plan["plan_hash"]) == 64
    assert plan["status"] == "AWAITING_APPROVAL"
    assert plan["plan_id"] == plan["action_id"]
    assert plan["items"] == plan["targets"]
    assert plan["preconditions"]["state_sha256"] == sha256_json(
        {
            "targets": plan["targets"],
            "execution_payload": plan["parameters"]["execution_payload"],
        }
    )

    tampered = control_plane_client.post(
        f"/api/action-requests/{plan['action_id']}/approve",
        json={
            "plan_hash": "0" * 64,
            "confirmation": plan["confirm_phrase"],
        },
    )
    assert tampered.status_code == 409
    assert tampered.json()["error"] == "plan_integrity_failed"

    approved = control_plane_client.post(
        f"/api/action-requests/{plan['action_id']}/approve",
        json={
            "plan_hash": plan["plan_hash"],
        },
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"
    assert len(approved.json()["approvals"]) == 1

    replay = control_plane_client.post(
        f"/api/action-requests/{plan['action_id']}/approve",
        json={
            "plan_hash": plan["plan_hash"],
            "confirmation": plan["confirm_phrase"],
        },
    )
    assert replay.status_code == 409
    assert len(
        control_plane_client.get(
            f"/api/action-requests/{plan['action_id']}"
        ).json()["approvals"]
    ) == 1
    listing = control_plane_client.get("/api/action-requests").json()
    assert listing["count"] == 1
    assert listing["as_of"]
    mission = control_plane_client.get("/api/mission-control").json()
    assert mission["data"]["pending_approvals"] == 0
    assert mission["as_of"]
    cached_mission = control_plane_client.get("/api/mission-control").json()
    assert cached_mission["cached"] is True


def test_viewer_findings_mask_pat_resource_and_json_evidence(
    control_plane_client,
    monkeypatch,
):
    deps.get_control_plane_repository().add_finding(
        {
            "workspace_id": "local",
            "environment": "dev",
            "run_ts": "2026-07-17T12:00:00+00:00",
            "area": "security",
            "check_name": "stale-token",
            "resource": "pat-raw-id",
            "reason": "token exceeds maximum age",
            "action": "token-revoke",
            "affected_resources_json": json.dumps(
                [{"resource_id": "pat-raw-id"}]
            ),
            "evidence_json": json.dumps(
                {
                    "token_id": "pat-raw-id",
                    "created_by": "owner@example.com",
                    "age_days": 120,
                }
            ),
        }
    )
    monkeypatch.setenv("DBX_PLATFORM_LOCAL_ROLES", "viewer")
    deps.get_identity_verifier.cache_clear()
    response = control_plane_client.get("/api/findings")
    assert response.status_code == 200
    finding = response.json()["data"][0]
    assert finding["resource"] == "[redacted]"
    assert finding["affected_resources"] == [{"resource_id": "[redacted]"}]
    assert finding["evidence"]["token_id"] == "[redacted]"
    assert finding["evidence"]["created_by"] == "[redacted]"
    assert finding["evidence"]["age_days"] == 120


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_approval_decision_rechecks_live_group_membership(
    control_plane_client,
    monkeypatch,
    decision,
):
    monkeypatch.setenv("DBX_PLATFORM_LOCAL_IDENTITY", "false")
    deps.get_identity_verifier.cache_clear()
    groups = ["dbx-platform-operators", "dbx-platform-approvers"]
    workspace = deps.get_ws()
    workspace.config.host = "https://workspace.example"
    user_workspace = MagicMock()

    def identity(*_args, **_kwargs):
        return {
            "id": "user-1",
            "userName": "operator@example.com",
            "groups": [{"display": group} for group in groups],
        }

    user_workspace.api_client.do.side_effect = identity
    verifier = IdentityVerifier(
        lambda: workspace,
        user_workspace_client_factory=lambda _host, _token: user_workspace,
    )
    def verifier_factory():
        return verifier

    verifier_factory.cache_clear = lambda: None
    monkeypatch.setattr(deps, "get_identity_verifier", verifier_factory)
    headers = {"X-Forwarded-Access-Token": "opaque-user-token"}
    planned = control_plane_client.post(
        "/api/action-requests/plan",
        headers=headers,
        json={"action_type": "stale-clusters"},
    ).json()
    groups.remove("dbx-platform-approvers")
    body = {"plan_hash": planned["plan_hash"]}
    if decision == "approve":
        body["confirm"] = planned["confirm_phrase"]
    else:
        body["reason"] = "not now"
    response = control_plane_client.post(
        f"/api/action-requests/{planned['action_id']}/{decision}",
        headers=headers,
        json=body,
    )
    assert response.status_code == 403
    assert response.json()["error"] == "unauthorized"
    stored = control_plane_client.get(
        f"/api/action-requests/{planned['action_id']}",
        headers=headers,
    ).json()
    assert stored["status"] == "AWAITING_APPROVAL"
    assert stored["approvals"] == []


def test_manual_job_run_is_planned_not_executed(control_plane_client):
    response = control_plane_client.post(
        "/api/action-requests/plan",
        json={
            "action": "run-job",
            "parameters": {
                "job_id": 7,
                "job_name": "[dbx-platform] cost-usage-report",
            },
        },
    )
    assert response.status_code == 200
    plan = response.json()
    assert plan["action"] == "run-job"
    assert len(plan["items"]) == 1
    target = plan["items"][0]
    assert target["resource_type"] == "JOB"
    assert target["resource_id"] == "7"
    assert target["job_id"] == 7
    assert target["name"] == "[dbx-platform] cost-usage-report"
    assert target["action"] == "RUN_NOW"
    assert target["job_state"]["settings"]["tasks"][0]["task_key"] == "report"
    assert target["job_state"]["run_as_user_name"] == "platform-runner@example.com"
    assert target["settings_sha256"] == sha256_json(target["job_state"])
    assert plan["status"] == "AWAITING_APPROVAL"


def _seed_decision_action(
    repository,
    *,
    action_id: str,
    now: datetime,
    risk: RiskLevel,
    target_id: str,
    action_type: str = "stale-clusters",
    target_field: str = "cluster_id",
) -> ActionRequest:
    action = ActionRequest.create(
        action_type=action_type,
        workspace_id="local",
        environment="dev",
        targets=[{target_field: target_id}],
        parameters={},
        preconditions={"state_sha256": sha256_json([target_id])},
        before_state=[{target_field: target_id}],
        after_state={"state": "TERMINATED"},
        impact={"summary": {"clusters": 1}, "target_count": 1},
        rollback={"supported": False},
        verification={"strategy": "re-read"},
        risk=risk,
        proposer=_actor("proposer"),
        now=now,
    )
    action.action_id = action_id
    action.plan_hash = action.calculated_hash()
    repository.create_action(action)
    return action


def test_mission_control_decision_queue_is_open_ranked_and_read_only(
    control_plane_client,
):
    repository = deps.get_control_plane_repository()
    now = control_plane.utc_now()
    high = _seed_decision_action(
        repository,
        action_id="action-high",
        now=now - timedelta(minutes=1),
        risk=RiskLevel.HIGH,
        target_id="cluster-high",
    )
    medium_soon = _seed_decision_action(
        repository,
        action_id="action-medium-soon",
        now=now - timedelta(minutes=12),
        risk=RiskLevel.MEDIUM,
        target_id="cluster-medium-soon",
    )
    medium_later = _seed_decision_action(
        repository,
        action_id="action-medium-later",
        now=now - timedelta(minutes=2),
        risk=RiskLevel.MEDIUM,
        target_id="cluster-medium-later",
    )
    low = _seed_decision_action(
        repository,
        action_id="action-low",
        now=now - timedelta(minutes=3),
        risk=RiskLevel.LOW,
        target_id="cluster-low",
    )
    derived_expired = _seed_decision_action(
        repository,
        action_id="action-derived-expired",
        now=now - timedelta(minutes=16),
        risk=RiskLevel.HIGH,
        target_id="cluster-derived-expired",
    )
    raw_expired = _seed_decision_action(
        repository,
        action_id="action-raw-expired",
        now=now - timedelta(minutes=30),
        risk=RiskLevel.HIGH,
        target_id="cluster-raw-expired",
    )
    approved_expired = _seed_decision_action(
        repository,
        action_id="action-approved-expired",
        now=now - timedelta(minutes=20),
        risk=RiskLevel.MEDIUM,
        target_id="cluster-approved-expired",
    )
    repository.transition(
        raw_expired.action_id,
        expected={ActionStatus.AWAITING_APPROVAL},
        target=ActionStatus.EXPIRED,
        actor_id="test",
    )
    repository.transition(
        approved_expired.action_id,
        expected={ActionStatus.AWAITING_APPROVAL},
        target=ActionStatus.APPROVED,
        actor_id="test",
    )
    repository.add_finding(
        {
            "finding_id": "finding-open",
            "workspace_id": "local",
            "environment": "dev",
            "pillar": "SECURITY",
            "severity": "HIGH",
            "confidence": 0.9,
            "check_name": "stale-cluster",
            "reason": "Cluster is stale.",
            "state": "OPEN",
            "proposed_action_type": "stale-clusters",
            "affected_resources_json": json.dumps(
                [{"cluster_id": "cluster-high"}]
            ),
            "freshness_at": "2026-07-18T12:00:00Z",
        }
    )
    repository.add_finding(
        {
            "finding_id": "finding-resolved",
            "workspace_id": "local",
            "environment": "dev",
            "pillar": "COST",
            "severity": "LOW",
            "state": "RESOLVED",
            "proposed_action_type": "stale-clusters",
            "affected_resources_json": json.dumps(
                [{"cluster_id": "cluster-high"}]
            ),
        }
    )
    before = {
        action.action_id: (
            action.plan_hash,
            len(repository.list_events(action.action_id)),
        )
        for action in (
            high,
            medium_soon,
            medium_later,
            low,
            derived_expired,
            raw_expired,
            approved_expired,
        )
    }
    cache.clear()

    response = control_plane_client.get("/api/mission-control")

    assert response.status_code == 200
    data = response.json()["data"]
    queue = data["decision_queue"]
    assert data["pending_approvals"] == 4
    assert data["findings"]["data"]["total"] == 1
    assert [row["finding_id"] for row in data["decisions"]] == [
        "finding-open"
    ]
    assert queue["ranking"] == "risk-expiry-created-v1"
    assert queue["active_count"] == 4
    assert queue["expiring_soon_count"] == 1
    assert queue["expired_count"] == 3
    assert [row["action_id"] for row in queue["items"]] == [
        "action-high",
        "action-medium-soon",
        "action-medium-later",
        "action-low",
    ]
    assert queue["items"][0]["evidence_summary"] == {
        "matched_count": 1,
        "pillars": ["SECURITY"],
        "freshest_at": "2026-07-18T12:00:00Z",
        "coverage_status": "MATCHED",
    }

    expired_listing = control_plane_client.get(
        "/api/action-requests",
        params={"status": "EXPIRED"},
    ).json()
    assert {
        row["action_id"] for row in expired_listing["data"]
    } == {
        "action-approved-expired",
        "action-derived-expired",
        "action-raw-expired",
    }
    detail = control_plane_client.get(
        f"/api/action-requests/{derived_expired.action_id}"
    ).json()
    assert detail["status"] == "AWAITING_APPROVAL"
    assert detail["raw_status"] == "AWAITING_APPROVAL"
    assert detail["effective_status"] == "EXPIRED"
    assert detail["can_approve"] is False
    assert "Create a new exact plan" in detail["expiry_guidance"]

    for action_id, (plan_hash, event_count) in before.items():
        current = repository.get_action(action_id)
        assert current is not None
        assert current.plan_hash == plan_hash
        assert len(repository.list_events(action_id)) == event_count


def test_action_detail_correlates_bounded_scoped_evidence(
    control_plane_client,
):
    repository = deps.get_control_plane_repository()
    action = _seed_decision_action(
        repository,
        action_id="action-evidence",
        now=control_plane.utc_now() - timedelta(minutes=1),
        risk=RiskLevel.MEDIUM,
        target_id="cluster-evidence",
    )
    for index in range(51):
        repository.add_finding(
            {
                "finding_id": f"finding-{index:02d}",
                "workspace_id": "local",
                "environment": "dev",
                "pillar": "SECURITY" if index % 2 else "COST",
                "severity": "HIGH",
                "confidence": 0.8,
                "check_name": "stale-cluster",
                "reason": "Current evidence for the exact cluster.",
                "state": "OPEN" if index else "RESOLVED",
                "proposed_action_type": (
                    "stale-clusters" if index != 1 else "review"
                ),
                "affected_resources_json": json.dumps(
                    [{"resource_id": "cluster-evidence"}]
                ),
                "freshness_at": f"2026-07-18T12:{index:02d}:00Z",
            }
        )
    repository.add_finding(
        {
            "finding_id": "foreign-finding",
            "workspace_id": "local",
            "environment": "prod",
            "pillar": "SECURITY",
            "state": "OPEN",
            "proposed_action_type": "stale-clusters",
            "affected_resources_json": json.dumps(
                [{"resource_id": "cluster-evidence"}]
            ),
        }
    )

    response = control_plane_client.get(
        f"/api/action-requests/{action.action_id}"
    )

    assert response.status_code == 200
    correlation = response.json()["evidence_correlation"]
    assert correlation["coverage_status"] == "MATCHED"
    assert correlation["total"] == 51
    assert correlation["truncated"] is True
    assert len(correlation["items"]) == 50
    assert "foreign-finding" not in {
        row["finding_id"] for row in correlation["items"]
    }
    assert {
        row["match_type"] for row in correlation["items"]
    } == {"supports_action", "same_target"}


def test_action_evidence_masks_sensitive_token_fields_for_viewer(
    control_plane_client,
    monkeypatch,
):
    repository = deps.get_control_plane_repository()
    action = _seed_decision_action(
        repository,
        action_id="action-token-evidence",
        now=control_plane.utc_now() - timedelta(minutes=1),
        risk=RiskLevel.HIGH,
        target_id="pat-secret-id",
        action_type="token-revoke",
        target_field="token_id",
    )
    repository.add_finding(
        {
            "finding_id": "token-finding",
            "workspace_id": "local",
            "environment": "dev",
            "pillar": "SECURITY",
            "check_name": "stale-token",
            "owner": "owner@example.com",
            "reason": "Token exceeds the configured maximum age.",
            "state": "OPEN",
            "proposed_action_type": "token-revoke",
            "affected_resources_json": json.dumps(
                [{"token_id": "pat-secret-id"}]
            ),
        }
    )
    monkeypatch.setenv("DBX_PLATFORM_LOCAL_ROLES", "viewer")
    deps.get_identity_verifier.cache_clear()

    response = control_plane_client.get(
        f"/api/action-requests/{action.action_id}"
    )

    assert response.status_code == 200
    item = response.json()["evidence_correlation"]["items"][0]
    assert item["owner"] == "[redacted]"
    assert item["affected_resources"] == [{"resource_id": "[redacted]"}]


def test_mission_and_action_detail_degrade_evidence_independently(
    control_plane_client,
    monkeypatch,
):
    repository = deps.get_control_plane_repository()
    action = _seed_decision_action(
        repository,
        action_id="action-degraded-evidence",
        now=control_plane.utc_now() - timedelta(minutes=1),
        risk=RiskLevel.MEDIUM,
        target_id="cluster-degraded",
    )

    def unavailable_findings(**_kwargs):
        raise RuntimeError("findings unavailable")

    monkeypatch.setattr(repository, "list_findings", unavailable_findings)
    cache.clear()

    mission = control_plane_client.get("/api/mission-control")
    detail = control_plane_client.get(
        f"/api/action-requests/{action.action_id}"
    )

    assert mission.status_code == 200
    mission_data = mission.json()["data"]
    assert mission_data["decision_queue"]["items"][0]["evidence_summary"][
        "coverage_status"
    ] == "UNAVAILABLE"
    assert next(
        source
        for source in mission_data["data_health"]
        if source["source"] == "Platform findings"
    )["status"] == "unavailable"
    assert detail.status_code == 200
    assert detail.json()["evidence_correlation"] == {
        "coverage_status": "UNAVAILABLE",
        "total": 0,
        "truncated": False,
        "items": [],
    }


def test_manual_job_target_rename_invalidates_approval(control_plane_client):
    planned = control_plane_client.post(
        "/api/action-requests/plan",
        json={"action": "run-job", "parameters": {"job_id": 7}},
    ).json()
    control_plane_client.app.state.test_job.settings.name = (
        "[dbx-platform] renamed-cost-report"
    )
    response = control_plane_client.post(
        f"/api/action-requests/{planned['action_id']}/approve",
        json={
            "plan_hash": planned["plan_hash"],
            "confirm": planned["confirm_phrase"],
        },
    )
    assert response.status_code == 409
    assert response.json()["error"] == "preconditions_changed"
    action = control_plane_client.get(
        f"/api/action-requests/{planned['action_id']}"
    ).json()
    assert action["status"] == "STALE"


def test_manual_job_task_or_compute_drift_invalidates_approval(
    control_plane_client,
):
    planned = control_plane_client.post(
        "/api/action-requests/plan",
        json={"action": "run-job", "parameters": {"job_id": 7}},
    ).json()
    job = control_plane_client.app.state.test_job
    job.settings.as_dict.side_effect = lambda: {
        "name": job.settings.name,
        "run_as": {"user_name": job.run_as_user_name},
        "tasks": [
            {
                "task_key": "report",
                "existing_cluster_id": "unexpected-shared-cluster",
                "notebook_task": {"notebook_path": "/Workspace/platform/report-v2"},
            }
        ],
    }
    response = control_plane_client.post(
        f"/api/action-requests/{planned['action_id']}/approve",
        json={
            "plan_hash": planned["plan_hash"],
            "confirm": planned["confirm_phrase"],
        },
    )
    assert response.status_code == 409
    assert response.json()["error"] == "preconditions_changed"


def _budget_parameters(**overrides):
    current_month = datetime.now(UTC).strftime("%Y-%m")
    values = {
        "scope_type": "team",
        "scope_value": "platform",
        "cost_basis": "AZURE_ACTUAL",
        "month": current_month,
        "currency": "CAD",
        "amount": 2500,
        "warning_threshold_pct": 80,
        "critical_threshold_pct": 100,
    }
    values.update(overrides)
    return values


def test_budget_plan_is_exact_read_only_and_revalidates_current_row(monkeypatch):
    reads = []
    current = {
        "budget_id": "budget-existing",
        "workspace_id": "local",
        "environment": "dev",
        "scope_type": "team",
        "scope_value": "platform",
        "cost_basis": "AZURE_ACTUAL",
        "month": datetime.now(UTC).date().replace(day=1),
        "currency": "CAD",
        "amount": 2000,
        "warning_pct": 75,
        "critical_pct": 95,
        "status": "ACTIVE",
        "plan_hash": "prior",
        "updated_by": "operator-1",
        "updated_at": datetime.now(UTC),
    }

    def read(budget_id, workspace_id, environment):
        reads.append((budget_id, workspace_id, environment))
        return control_plane._normalize_current_budget(current)

    monkeypatch.setattr(control_plane, "_read_budget_by_id", read)
    spec = control_plane._planner(
        "configure-budget",
        _budget_parameters(budget_id="budget-existing"),
    )
    assert reads == [("budget-existing", "local", "dev")]
    assert spec.targets == [
        {
            "resource_type": "LLM_BUDGET",
            "resource_id": "budget-existing",
            "budget_id": "budget-existing",
            "scope_type": "team",
            "scope_value": "platform",
            "cost_basis": "AZURE_ACTUAL",
            "month": datetime.now(UTC).strftime("%Y-%m-01"),
            "currency": "CAD",
            "action": "UPDATE",
        }
    ]
    assert spec.before_state["budget"]["amount"] == 2000
    assert spec.after_state["budget"]["amount"] == 2500
    assert (
        spec.parameters["execution_payload"]["expected_before"]["plan_hash"]
        == "prior"
    )
    assert spec.preconditions["state_sha256"] == sha256_json(
        {
            "targets": spec.targets,
            "execution_payload": spec.parameters["execution_payload"],
        }
    )

    action = ActionRequest.create(
        action_type=spec.action_type,
        workspace_id="local",
        environment="dev",
        targets=spec.targets,
        parameters=spec.parameters,
        preconditions=spec.preconditions,
        before_state=spec.before_state,
        after_state=spec.after_state,
        impact=spec.impact,
        rollback=spec.rollback,
        verification=spec.verification,
        risk=spec.risk,
        proposer=_actor("proposer"),
    )
    current["amount"] = 2100
    assert (
        control_plane._revalidate(action)["state_sha256"]
        != action.preconditions["state_sha256"]
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"scope_type": "user"}, "scope_type"),
        ({"scope_value": ""}, "missing required"),
        ({"cost_basis": "BLENDED"}, "cost_basis"),
        ({"currency": "cad"}, "uppercase"),
        ({"amount": 0}, "greater than zero"),
        ({"warning_threshold_pct": 101}, "0 to 100"),
        (
            {"warning_threshold_pct": 90, "critical_threshold_pct": 80},
            "greater than or equal",
        ),
        ({"unexpected": True}, "unsupported parameters"),
    ],
)
def test_budget_plan_rejects_ambiguous_or_unsafe_input(
    monkeypatch, overrides, message
):
    monkeypatch.setattr(control_plane, "_read_budget_by_id", lambda *_: None)
    with pytest.raises(ValueError, match=message):
        control_plane._planner(
            "configure-budget",
            _budget_parameters(**overrides),
        )


def test_runtime_alias_uses_controller_plan_and_submits_after_approval(
    control_plane_client,
    monkeypatch,
):
    repository = deps.get_control_plane_repository()

    class FakeController:
        def __init__(self):
            self.execution = None

        def submit_plan(self, action_type):
            assert action_type == "runtime.hibernate"
            return _runtime_action(repository)

        def submit_execute(self, action):
            self.execution = action
            return 9001

    controller = FakeController()
    monkeypatch.setattr(deps, "get_runtime_controller_client", lambda: controller)
    planned = control_plane_client.post(
        "/api/action-requests/plan",
        json={"action": "hibernate"},
    )
    assert planned.status_code == 200
    plan = planned.json()
    assert plan["action_type"] == "runtime.hibernate"
    assert "resources" in plan["preconditions"]

    approved = control_plane_client.post(
        f"/api/action-requests/{plan['action_id']}/approve",
        json={"plan_hash": plan["plan_hash"]},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"
    assert approved.json()["execution_id"] == 9001
    assert controller.execution.action_id == plan["action_id"]

"""Safety and behavior tests for the Platform Console app (FastAPI backend).

Two layers, both offline (the SDK is mocked, per repo convention):

- Static AST/source checks: no module constructs a workspace client at import
  time, and the package's mutator functions are referenced only by the single
  guarded path (backend/routers/actions.py).
- TestClient behavior: mutations are POST-only, the actions gate defaults to
  off, apply requires a fresh single-use plan plus a typed confirm phrase,
  and errors map to the friendly taxonomy instead of raw tracebacks.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

APP_DIR = Path(__file__).resolve().parent.parent / "apps" / "platform-console"
APP_FILES = sorted(
    p
    for p in APP_DIR.rglob("*.py")
    if "frontend" not in p.parts and ".venv" not in p.parts
)

sys.path.insert(0, str(APP_DIR))

from backend import cache, deps  # noqa: E402
from backend.proposals import parse_proposals  # noqa: E402
from backend.routers import actions  # noqa: E402

from dbx_platform.config import Settings  # noqa: E402
from dbx_platform.system_tables import SystemTablesUnavailableError  # noqa: E402

FORBIDDEN_TOP_LEVEL_CALLS = {"get_client", "WorkspaceClient"}

# The only file allowed to reference the package's mutating functions.
GUARDED_PATH = APP_DIR / "backend" / "routers" / "actions.py"
MUTATORS = ("apply_cluster_findings", "pause_job", "revoke_tokens", "apply_policy_plan")


# --- static safety checks ---------------------------------------------------

def _top_level_calls(tree: ast.Module) -> set[str]:
    """Names called at module scope — bodies of function/class defs excluded."""
    names = set()
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name):
                    names.add(fn.id)
                elif isinstance(fn, ast.Attribute):
                    names.add(fn.attr)
    return names


def test_app_source_exists():
    assert (APP_DIR / "app.yaml").exists()
    assert (APP_DIR / "pyproject.toml").exists()
    assert (APP_DIR / "uv.lock").exists()
    assert not (APP_DIR / "requirements.txt").exists()
    assert len(APP_FILES) >= 4


def test_no_workspace_client_constructed_at_import_time():
    for path in APP_FILES:
        calls = _top_level_calls(ast.parse(path.read_text()))
        assert not (calls & FORBIDDEN_TOP_LEVEL_CALLS), (
            f"{path.name} touches the workspace at import time"
        )


def test_mutators_only_referenced_by_the_guarded_actions_router():
    for path in APP_FILES:
        if path == GUARDED_PATH:
            continue
        source = path.read_text()
        for mutator in MUTATORS:
            assert mutator not in source, (
                f"{path.relative_to(APP_DIR)} references {mutator}; mutations are "
                "allowed only in backend/routers/actions.py"
            )


def test_action_registry_is_exactly_the_four_conservative_actions():
    assert set(actions.REGISTRY) == {
        "stale-clusters", "orphaned-jobs", "token-revoke", "policy-sync"
    }


def test_app_yaml_launches_the_backend():
    config = yaml.safe_load((APP_DIR / "app.yaml").read_text())
    assert config["command"] == ["python", "main.py"]
    bundle_config = yaml.safe_load(
        (APP_DIR.parent.parent / "resources" / "app.yml").read_text()
    )
    assert bundle_config["resources"]["apps"]["platform_console"][
        "user_api_scopes"
    ] == ["sql"]
    project = (APP_DIR / "pyproject.toml").read_text()
    assert '"fastapi==0.139.2"' in project
    assert '"mlflow==3.14.0"' in project
    assert 'dbx-platform = { path = "wheels/' in project


def test_bundle_artifact_build_uses_managed_environment():
    bundle = yaml.safe_load((APP_DIR.parent.parent / "databricks.yml").read_text())
    assert bundle["artifacts"]["default"]["build"] == "uv run python -m build --wheel"


def test_app_and_controller_share_name_without_resource_cycle():
    root = APP_DIR.parent.parent
    app_resource = yaml.safe_load((root / "resources" / "app.yml").read_text())
    runtime_resource = yaml.safe_load(
        (root / "resources" / "runtime_control.yml").read_text()
    )
    app_name = app_resource["resources"]["apps"]["platform_console"]["name"]
    parameters = runtime_resource["resources"]["jobs"]["power_controller"][
        "tasks"
    ][0]["spark_python_task"]["parameters"]

    assert app_name == "${var.platform_console_name}"
    app_name_index = parameters.index("--app-name") + 1
    assert parameters[app_name_index] == "${var.platform_console_name}"
    assert "${resources.apps.platform_console.name}" not in parameters


def test_chat_model_is_bound_to_the_app_with_query_only_access():
    root = APP_DIR.parent.parent
    bundle = yaml.safe_load((root / "databricks.yml").read_text())
    resource = yaml.safe_load(
        (root / "resources" / "app.yml").read_text()
    )["resources"]["apps"]["platform_console"]
    assert bundle["variables"]["chat_model"]["default"].startswith("databricks-")
    assert {
        "name": "DBX_PLATFORM_CHAT_ENDPOINT",
        "value_from": "chat-model",
    } in resource["config"]["env"]
    chat_resource = next(
        item for item in resource["resources"] if item["name"] == "chat-model"
    )
    assert chat_resource["serving_endpoint"] == {
        "name": "${var.chat_model}",
        "permission": "CAN_QUERY",
    }


# --- TestClient behavior ----------------------------------------------------

@pytest.fixture()
def ws(monkeypatch) -> MagicMock:
    mock = MagicMock()
    monkeypatch.setattr(deps, "get_ws", lambda: mock)
    monkeypatch.setattr(deps, "get_settings", lambda: Settings(warehouse_id="wh-test"))
    cache.clear()
    return mock


@pytest.fixture()
def client(ws, monkeypatch):
    monkeypatch.setenv("DBX_PLATFORM_LOCAL_IDENTITY", "true")
    monkeypatch.setenv("DBX_PLATFORM_LOCAL_ACTOR_ID", "test-operator")
    monkeypatch.setenv("DBX_PLATFORM_LOCAL_ROLES", "operator")
    deps.get_identity_verifier.cache_clear()
    from backend.app import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        yield test_client
    deps.get_identity_verifier.cache_clear()


def test_app_construction_never_touches_the_workspace(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(deps, "get_ws", lambda: mock)
    from backend.app import create_app

    create_app()
    mock.assert_not_called()
    assert not mock.method_calls


def test_cost_routes_are_registered(client):
    paths = {
        getattr(route, "path", "")
        for route in _iter_routes(client.app)
    }
    assert "/api/cost/products" in paths
    assert {
        "/api/llm-cost/summary",
        "/api/llm-cost/timeseries",
        "/api/llm-cost/breakdown",
        "/api/llm-cost/efficiency",
        "/api/llm-cost/tokenomics",
        "/api/llm-cost/data-health",
    }.issubset(paths)


def _iter_routes(container):
    """Walk app routes, unwrapping FastAPI's included-router containers."""
    for route in getattr(container, "routes", []):
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _iter_routes(inner)
        else:
            yield route


def test_every_mutating_route_is_post_only(client):
    get_paths, post_paths = set(), set()
    for route in _iter_routes(client.app):
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", "")
        if "GET" in methods:
            get_paths.add(path)
        if "POST" in methods:
            post_paths.add(path)
    assert post_paths - {"/api/{path:path}"} == {
        "/api/action-requests/plan",
        "/api/action-requests/{action_id}/approve",
        "/api/action-requests/{action_id}/reject",
        "/api/jobs/{job_id}/run_now",
        "/api/digest/generate",
        "/api/chat",
    }
    assert (get_paths & post_paths) <= {
        "/api/{path:path}"
    }, "only the stable JSON 404 catch-all may accept multiple methods"


def test_health_reports_actions_disabled_by_default(client, monkeypatch):
    monkeypatch.delenv("DBX_PLATFORM_CONSOLE_ACTIONS", raising=False)
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["actions_enabled"] is False


def test_unknown_api_returns_stable_json_capability_error(client):
    response = client.get("/api/not-a-real-capability")

    assert response.status_code == 404
    assert response.json()["error"] == "capability_not_available"


def test_production_runtime_auto_migration_fails_closed(monkeypatch):
    monkeypatch.setenv("DATABRICKS_APP_NAME", "dbx-platform")
    monkeypatch.setenv("DBX_PLATFORM_CONTROL_PLANE_REPOSITORY", "sql")
    monkeypatch.setenv("DBX_PLATFORM_CONTROL_PLANE_AUTO_MIGRATE", "true")
    deps.get_control_plane_repository.cache_clear()

    with pytest.raises(RuntimeError, match="deployment-only schema_migrations"):
        deps.get_control_plane_repository()

    deps.get_control_plane_repository.cache_clear()


def test_operational_api_requires_verified_user_but_health_stays_public(
    client,
    ws,
    monkeypatch,
):
    monkeypatch.setenv("DBX_PLATFORM_LOCAL_IDENTITY", "false")
    deps.get_identity_verifier.cache_clear()
    ws.api_client.do.return_value = {}
    response = client.get("/api/config")
    assert response.status_code == 401
    body = response.json()
    assert body["error"] == "unauthenticated"
    assert "user authorization is enabled" in body["hint"]
    assert "restarted after scope changes" in body["hint"]
    assert client.get("/api/health").status_code == 200


def test_viewer_operational_responses_mask_pat_and_identity_fields(
    client,
    ws,
    monkeypatch,
):
    monkeypatch.setenv("DBX_PLATFORM_LOCAL_ROLES", "viewer")
    deps.get_identity_verifier.cache_clear()
    deps.get_control_plane_repository().add_finding(
        {
            "workspace_id": "local",
            "environment": "dev",
            "run_ts": "2026-07-17T12:00:00+00:00",
            "area": "security",
            "check_name": "stale-pat",
            "resource": "pat-secret-id",
            "reason": "token exceeds the configured maximum age",
            "action": "token-revoke",
            "affected_resources_json": json.dumps(
                [{"resource_id": "pat-secret-id"}]
            ),
            "evidence_json": json.dumps(
                {
                    "token_id": "pat-secret-id",
                    "created_by": "owner@example.com",
                    "comment": "automation",
                }
            ),
        }
    )
    cache.clear()
    response = client.get("/api/security/token-audit")
    assert response.status_code == 200
    row = response.json()["data"][0]
    assert row["resource"] == "[redacted]"
    assert row["affected_resources"] == [{"resource_id": "[redacted]"}]
    assert row["evidence"]["token_id"] == "[redacted]"
    assert row["evidence"]["created_by"] == "[redacted]"
    assert row["evidence"]["comment"] == "automation"


def test_legacy_action_http_routes_are_removed(client):
    # The SPA's GET-only catch-all can make an unmatched POST surface as 405;
    # route enumeration above proves neither legacy handler is registered.
    assert client.post("/api/actions/stale-clusters/plan").status_code in {404, 405}
    assert client.post("/api/actions/stale-clusters/apply").status_code in {404, 405}


def test_run_now_refuses_jobs_outside_the_platform_filter(client, ws):
    repo = deps.get_control_plane_repository()
    repo.add_managed_resource({
        "workspace_id": "local",
        "environment": "dev",
        "resource_id": "7",
        "resource_type": "JOB",
        "ownership": "BUNDLE",
        "protected": False,
    })
    job = MagicMock()
    job.job_id = 7
    job.settings.name = "[dbx-platform] cost-usage-report"
    job.settings.schedule = SimpleNamespace(
        pause_status=SimpleNamespace(value="PAUSED")
    )
    job.settings.as_dict.return_value = {
        "name": job.settings.name,
        "tasks": [{"task_key": "report"}],
    }
    job.run_as_user_name = "runner@example.com"
    other = MagicMock()
    other.job_id = 8
    other.settings.name = "someone-elses-etl"
    ws.jobs.list.return_value = [job, other]
    ws.jobs.get.return_value = job
    visible = client.get("/api/jobs", params={"refresh": "true"})
    assert visible.status_code == 200
    assert visible.json()["data"] == [
        {
            "job_id": 7,
            "name": "[dbx-platform] cost-usage-report",
            "schedule_status": "PAUSED",
            "schedule_type": "CRON",
        }
    ]
    assert client.post("/api/jobs/8/run_now").status_code == 404
    ws.jobs.run_now.assert_not_called()
    resp = client.post("/api/jobs/7/run_now")
    assert resp.status_code == 409
    assert resp.json()["error"] == "approval_required"
    ws.jobs.run_now.assert_not_called()


def test_protected_manual_job_is_admitted_only_by_exact_bound_id(
    client,
    ws,
    monkeypatch,
):
    monkeypatch.setenv("DBX_PLATFORM_GOVERNED_MANUAL_JOB_IDS", "91")
    training = MagicMock()
    training.job_id = 91
    training.settings.name = "[dbx-platform] cost-forecast-train"
    training.settings.as_dict.return_value = {
        "name": training.settings.name,
        "tasks": [{"task_key": "train"}],
    }
    training.run_as_user_name = "forecast-executor"
    unrelated = MagicMock()
    unrelated.job_id = 92
    unrelated.settings.name = "[dbx-platform] similarly-named-but-unbound"
    ws.jobs.list.return_value = [training, unrelated]
    ws.jobs.get.return_value = training

    allowed = client.post("/api/jobs/91/run_now")
    excluded = client.post("/api/jobs/92/run_now")

    assert allowed.status_code == 409
    assert allowed.json()["error"] == "approval_required"
    assert excluded.status_code == 404
    ws.jobs.run_now.assert_not_called()


def test_run_all_endpoint_is_not_available(client, ws):
    response = client.post("/api/jobs/run_all")

    assert response.status_code == 404
    assert response.json()["error"] == "capability_not_available"
    ws.jobs.run_now.assert_not_called()


def test_system_tables_error_maps_to_friendly_503(client, monkeypatch):
    from backend.routers import cost as cost_router

    def boom(*args, **kwargs):
        raise SystemTablesUnavailableError("system tables not enabled")

    monkeypatch.setattr(cost_router.cost, "usage_report", boom)
    resp = client.get("/api/cost/usage")
    assert resp.status_code == 503
    assert resp.json()["error"] == "system_tables_unavailable"
    assert "system tables not enabled" not in resp.json()["message"]


def test_missing_warehouse_maps_to_friendly_503(client, monkeypatch):
    monkeypatch.setattr(deps, "get_settings", lambda: Settings(warehouse_id=""))
    monkeypatch.delenv("DBX_PLATFORM_WAREHOUSE_ID", raising=False)
    resp = client.get("/api/cost/usage")
    assert resp.status_code == 503
    assert resp.json()["error"] == "warehouse_not_configured"


def test_chat_degrades_when_the_backend_agent_is_unavailable(client, monkeypatch):
    agent = MagicMock()
    agent.invoke.side_effect = RuntimeError("RESOURCE_DOES_NOT_EXIST")
    monkeypatch.setattr(deps, "get_platform_agent", lambda: agent)
    resp = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 503
    assert resp.json()["error"] == "agent_unavailable"


def test_chat_denies_viewers_before_invoking_app_sp_agent(
    client,
    monkeypatch,
):
    agent = MagicMock()
    monkeypatch.setattr(deps, "get_platform_agent", lambda: agent)
    monkeypatch.setenv("DBX_PLATFORM_LOCAL_ROLES", "viewer")
    deps.get_identity_verifier.cache_clear()
    resp = client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "show token owners"}]},
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "unauthorized"
    agent.invoke.assert_not_called()


def test_chat_parses_backend_agent_proposals(client, monkeypatch):
    agent = MagicMock()
    agent.invoke.return_value = (
        "Two stale clusters are burning money.\n"
        'ACTION_PROPOSAL:{"action": "stale-clusters", "count": 2}\n'
    )
    monkeypatch.setattr(deps, "get_platform_agent", lambda: agent)
    resp = client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "clean up"}],
            "context": {
                "route": "/security-risk",
                "query": "?severity=critical",
                "filters": {"severity": "critical"},
                "selected_resources": [{"resource_type": "TOKEN", "count": "2"}],
            },
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["message"] == "Two stale clusters are burning money."
    assert body["proposals"] == [{"kind": "action", "action": "stale-clusters", "count": 2}]
    invocation = agent.invoke.call_args.args[0]
    assert invocation[0]["role"] == "system"
    assert "Every factual claim must cite" in invocation[0]["content"]
    assert "PAGE_CONTEXT" in invocation[1]["content"]
    assert "/security-risk" in invocation[1]["content"]
    assert invocation[-1] == {"role": "user", "content": "clean up"}


# --- proposal marker parsing (pure) ----------------------------------------

def test_parse_proposals_handles_both_kinds_and_strips_markers():
    text = (
        "Run the security audit now.\n"
        'JOB_PROPOSAL:{"job_id": 12, "name": "[dbx-platform] security-audit"}\n'
        "And revoke the old tokens:\n"
        'ACTION_PROPOSAL:{"action": "token-revoke", "count": 3}\n'
        "Or kick everything off:\n"
        'JOB_PROPOSAL:{"all": true, "count": 11}\n'
    )
    clean, proposals = parse_proposals(text)
    assert "PROPOSAL" not in clean
    assert proposals == [
        {"kind": "job", "job_id": 12, "name": "[dbx-platform] security-audit"},
        {"kind": "action", "action": "token-revoke", "count": 3},
        {"kind": "job", "all": True, "count": 11},
    ]


def test_parse_proposals_leaves_malformed_markers_visible():
    text = "ACTION_PROPOSAL:{not json}"
    clean, proposals = parse_proposals(text)
    assert clean == text
    assert proposals == []

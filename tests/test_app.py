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
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

APP_DIR = Path(__file__).resolve().parent.parent / "apps" / "platform-console"
APP_FILES = sorted(p for p in APP_DIR.rglob("*.py") if "frontend" not in p.parts)

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
    assert (APP_DIR / "requirements.txt").exists()
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
    requirements = (APP_DIR / "requirements.txt").read_text()
    assert "--find-links wheels" in requirements
    assert "fastapi" in requirements


# --- TestClient behavior ----------------------------------------------------

@pytest.fixture()
def ws(monkeypatch) -> MagicMock:
    mock = MagicMock()
    monkeypatch.setattr(deps, "get_ws", lambda: mock)
    monkeypatch.setattr(deps, "get_settings", lambda: Settings(warehouse_id="wh-test"))
    cache.clear()
    return mock


@pytest.fixture()
def client(ws):
    from backend.app import create_app
    from fastapi.testclient import TestClient

    return TestClient(create_app(), raise_server_exceptions=False)


def test_app_construction_never_touches_the_workspace(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(deps, "get_ws", lambda: mock)
    from backend.app import create_app

    create_app()
    mock.assert_not_called()
    assert not mock.method_calls


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
    assert post_paths == {
        "/api/actions/{action}/plan",
        "/api/actions/{action}/apply",
        "/api/jobs/{job_id}/run_now",
        "/api/jobs/run_all",
        "/api/digest/generate",
        "/api/chat",
    }
    assert not (get_paths & post_paths), "no route may accept both GET and POST"


def test_health_reports_actions_disabled_by_default(client, monkeypatch):
    monkeypatch.delenv("DBX_PLATFORM_CONSOLE_ACTIONS", raising=False)
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["actions_enabled"] is False


@pytest.fixture()
def fake_action(monkeypatch):
    """A registry entry with a recording apply, so guard tests never touch
    the real mutators."""
    applied = []
    items = [{"cluster_id": "c-1", "action": "terminate"}]
    monkeypatch.setitem(actions.REGISTRY, "stale-clusters", {
        "plan": lambda: (items, items, {"terminate": 1}),
        "apply": lambda payload: (applied.append(payload) or ["terminated c-1"]),
    })
    return applied


def test_plan_works_while_actions_are_disabled(client, fake_action, monkeypatch):
    monkeypatch.delenv("DBX_PLATFORM_CONSOLE_ACTIONS", raising=False)
    body = client.post("/api/actions/stale-clusters/plan").json()
    assert body["actions_enabled"] is False
    assert body["confirm_phrase"] == "apply stale-clusters 1"
    assert body["items"] == [{"cluster_id": "c-1", "action": "terminate"}]


def test_apply_refused_when_gate_is_off(client, fake_action, monkeypatch):
    monkeypatch.delenv("DBX_PLATFORM_CONSOLE_ACTIONS", raising=False)
    plan = client.post("/api/actions/stale-clusters/plan").json()
    resp = client.post("/api/actions/stale-clusters/apply", json={
        "plan_id": plan["plan_id"], "confirm": plan["confirm_phrase"]})
    assert resp.status_code == 403
    assert resp.json()["error"] == "actions_disabled"
    assert fake_action == []


def test_apply_refuses_wrong_confirm_phrase(client, fake_action, monkeypatch):
    monkeypatch.setenv("DBX_PLATFORM_CONSOLE_ACTIONS", "true")
    plan = client.post("/api/actions/stale-clusters/plan").json()
    resp = client.post("/api/actions/stale-clusters/apply", json={
        "plan_id": plan["plan_id"], "confirm": "yes please"})
    assert resp.status_code == 409
    assert resp.json()["error"] == "confirmation_mismatch"
    assert fake_action == []


def test_apply_refuses_expired_plan(client, fake_action, monkeypatch):
    monkeypatch.setenv("DBX_PLATFORM_CONSOLE_ACTIONS", "true")
    plan = client.post("/api/actions/stale-clusters/plan").json()
    actions.plans._plans[plan["plan_id"]]["expires_at"] = 0
    resp = client.post("/api/actions/stale-clusters/apply", json={
        "plan_id": plan["plan_id"], "confirm": plan["confirm_phrase"]})
    assert resp.status_code == 410
    assert resp.json()["error"] == "plan_expired"
    assert fake_action == []


def test_apply_happy_path_is_single_use(client, fake_action, monkeypatch):
    monkeypatch.setenv("DBX_PLATFORM_CONSOLE_ACTIONS", "true")
    plan = client.post("/api/actions/stale-clusters/plan").json()
    request = {"plan_id": plan["plan_id"], "confirm": plan["confirm_phrase"]}
    resp = client.post("/api/actions/stale-clusters/apply", json=request)
    assert resp.status_code == 200
    assert resp.json()["applied"] == ["terminated c-1"]
    assert fake_action == [[{"cluster_id": "c-1", "action": "terminate"}]]
    # The plan is consumed: a retry must re-plan against current state.
    retry = client.post("/api/actions/stale-clusters/apply", json=request)
    assert retry.status_code == 404
    assert retry.json()["error"] == "plan_not_found"
    assert len(fake_action) == 1


def test_apply_without_body_is_rejected(client, monkeypatch):
    monkeypatch.setenv("DBX_PLATFORM_CONSOLE_ACTIONS", "true")
    assert client.post("/api/actions/stale-clusters/apply").status_code == 422


def test_unknown_action_is_404(client):
    assert client.post("/api/actions/delete-everything/plan").status_code == 404


def test_run_now_refuses_jobs_outside_the_platform_filter(client, ws):
    job = MagicMock()
    job.job_id = 7
    job.settings.name = "[dbx-platform] cost-usage-report"
    other = MagicMock()
    other.job_id = 8
    other.settings.name = "someone-elses-etl"
    ws.jobs.list.return_value = [job, other]
    assert client.post("/api/jobs/8/run_now").status_code == 404
    ws.jobs.run_now.assert_not_called()
    ws.jobs.run_now.return_value.run_id = 99
    resp = client.post("/api/jobs/7/run_now")
    assert resp.status_code == 200
    assert resp.json()["run_id"] == 99


def _mock_job(job_id: int, name: str) -> MagicMock:
    job = MagicMock()
    job.job_id = job_id
    job.settings.name = name
    return job


def test_run_all_triggers_only_platform_jobs_setup_first(client, ws):
    ws.jobs.list.return_value = [
        _mock_job(7, "[dbx-platform] cost-usage-report"),
        _mock_job(5, "[dbx-platform] dashboards-setup"),
        _mock_job(8, "someone-elses-etl"),
    ]
    run_ids = iter([101, 102])
    ws.jobs.run_now.side_effect = lambda job_id: MagicMock(run_id=next(run_ids))
    body = client.post("/api/jobs/run_all").json()
    assert body["count"] == 2
    assert body["failed"] == []
    submitted = [c.kwargs["job_id"] for c in ws.jobs.run_now.call_args_list]
    assert submitted == [5, 7], "dashboards-setup must be submitted first, job 8 never"


def test_run_all_captures_per_job_failures_and_continues(client, ws):
    ws.jobs.list.return_value = [
        _mock_job(5, "[dbx-platform] dashboards-setup"),
        _mock_job(7, "[dbx-platform] cost-usage-report"),
    ]

    def run_now(job_id):
        if job_id == 5:
            raise RuntimeError("PERMISSION_DENIED")
        return MagicMock(run_id=200)

    ws.jobs.run_now.side_effect = run_now
    body = client.post("/api/jobs/run_all").json()
    assert body["count"] == 1
    assert [r["job_id"] for r in body["runs"]] == [7]
    assert body["failed"] == [{
        "job_id": 5,
        "name": "[dbx-platform] dashboards-setup",
        "error": "PERMISSION_DENIED",
    }]


def test_system_tables_error_maps_to_friendly_503(client, monkeypatch):
    from backend.routers import cost as cost_router

    def boom(*args, **kwargs):
        raise SystemTablesUnavailableError("system tables not enabled")

    monkeypatch.setattr(cost_router.cost, "usage_report", boom)
    resp = client.get("/api/cost/usage")
    assert resp.status_code == 503
    assert resp.json()["error"] == "system_tables_unavailable"


def test_missing_warehouse_maps_to_friendly_503(client, monkeypatch):
    monkeypatch.setattr(deps, "get_settings", lambda: Settings(warehouse_id=""))
    monkeypatch.delenv("DBX_PLATFORM_WAREHOUSE_ID", raising=False)
    resp = client.get("/api/cost/usage")
    assert resp.status_code == 503
    assert resp.json()["error"] == "warehouse_not_configured"


def test_chat_degrades_when_the_agent_endpoint_is_missing(client, ws):
    ws.api_client.do.side_effect = RuntimeError("RESOURCE_DOES_NOT_EXIST")
    resp = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 503
    assert resp.json()["error"] == "agent_unavailable"


def test_chat_parses_agent_proposals(client, ws):
    ws.api_client.do.return_value = {
        "output": [{
            "type": "message",
            "content": [{"type": "output_text", "text": (
                "Two stale clusters are burning money.\n"
                'ACTION_PROPOSAL:{"action": "stale-clusters", "count": 2}\n'
            )}],
        }],
    }
    resp = client.post("/api/chat", json={"messages": [{"role": "user", "content": "clean up"}]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["message"] == "Two stale clusters are burning money."
    assert body["proposals"] == [{"kind": "action", "action": "stale-clusters", "count": 2}]


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

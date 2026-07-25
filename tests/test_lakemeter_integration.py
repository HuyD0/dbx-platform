from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "apps" / "platform-console"
sys.path.insert(0, str(APP_DIR))

from backend import lakemeter_database, lakemeter_integration  # noqa: E402
from backend.control_plane import Actor  # noqa: E402

from dbx_platform import lakemeter_migrations  # noqa: E402


def test_lakemeter_status_reports_pinned_contract(monkeypatch):
    monkeypatch.setattr(
        lakemeter_database,
        "schema_status",
        lambda expected: (False, None, "database_not_configured"),
    )

    result = lakemeter_integration.status()

    assert result["status"] == "unavailable"
    assert result["database_ready"] is False
    assert result["reason"] == "database_not_configured"
    assert result["upstream_version"] == "v0.1.0"
    assert result["required_schema_version"] == 1
    assert result["pricing_version"]


def test_forwarded_identity_headers_are_replaced_with_verified_actor():
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/estimates",
            "headers": [
                (b"x-forwarded-email", b"spoofed@example.com"),
                (b"x-forwarded-user", b"spoofed-id"),
                (b"x-forwarded-name", b"Spoofed User"),
                (b"x-forwarded-access-token", b"verified-but-not-forwarded"),
                (b"x-correlation-id", b"keep-me"),
            ],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
            "root_path": "",
        }
    )
    request.state.actor = Actor(
        actor_id="verified-id",
        email="verified@example.com",
        roles=frozenset({"authenticated", "viewer"}),
    )

    lakemeter_integration.canonicalize_identity_headers(request)
    headers = dict(request.scope["headers"])

    assert headers[b"x-forwarded-email"] == b"verified@example.com"
    assert headers[b"x-forwarded-user"] == b"verified-id"
    assert b"x-forwarded-name" not in headers
    assert b"x-forwarded-access-token" not in headers
    assert headers[b"x-correlation-id"] == b"keep-me"


def test_only_selected_upstream_routers_are_mounted():
    app = lakemeter_integration.build_upstream_app()

    def paths(container) -> set[str]:
        result = set()
        for route in getattr(container, "routes", []):
            inner = getattr(route, "original_router", None)
            if inner is not None:
                result.update(paths(inner))
            elif hasattr(route, "path"):
                result.add(route.path)
        return result

    registered = paths(app)

    assert "/estimates/" in registered
    assert "/line-items/" in registered
    assert "/workload-types" in registered
    assert "/export/estimate/{estimate_id}/excel" in registered
    assert "/chat/stream" in registered
    assert not any(path.startswith("/users") for path in registered)
    assert not any(path.startswith("/debug") for path in registered)
    assert app.docs_url is None
    assert app.openapi_url is None


def test_database_bridge_has_no_password_or_schema_fallback():
    source = (APP_DIR / "backend" / "lakemeter_database.py").read_text()

    assert "generate_database_credential" in source
    assert "DBX_LAKEMETER_ENDPOINT" in source
    assert "create_all" not in source
    assert "CREATE TABLE" not in source
    assert "secrets.get_secret" not in source
    assert "lakebase-password" not in source


def test_pinned_migration_inputs_are_complete():
    installer = lakemeter_migrations._vendor_file(
        "scripts", "install_lakemeter.py"
    ).read_text(encoding="utf-8")
    workloads = lakemeter_migrations._module_assignment(
        lakemeter_migrations._vendor_file(
            "backend", "app", "routes", "workload_types.py"
        ).read_text(encoding="utf-8"),
        "DEFAULT_WORKLOAD_TYPES",
    )

    assert len(
        lakemeter_migrations._assignment(
            installer, "_create_tables_inline", "table_stmts"
        )
    ) >= 10
    assert len(lakemeter_migrations._sync_table_sql(installer)) == 1
    assert len(lakemeter_migrations._function_sql()) == 14
    assert {item["workload_type"] for item in workloads} >= {
        "JOBS",
        "DBSQL",
        "LAKEBASE",
        "DATABRICKS_APPS",
        "AI_PARSE",
    }


def test_migration_executor_identity_match_is_exact():
    me = SimpleNamespace(
        id="scim-id",
        user_name="migration-client-id",
        application_id="application-id",
    )

    assert lakemeter_migrations._identity_matches(me, "migration-client-id")
    assert lakemeter_migrations._identity_matches(me, "application-id")
    assert not lakemeter_migrations._identity_matches(me, "prefix")

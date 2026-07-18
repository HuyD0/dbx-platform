import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from dbx_platform.dashboards import (
    TEMPLATE_NAMES,
    build_team_name_function_sql,
    dependency_health,
    render_template,
    setup_statements,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Objects the dashboards reference are written as `<catalog>.<schema>.<name>`.
# The committed dashboards render to dbx_dev.dbx_platform (this workspace has no
# `main` catalog — see config.Settings.dashboard_catalog).
_HELPER_OBJECT = re.compile(r"dbx_dev\.dbx_platform\.([A-Za-z_][A-Za-z0-9_]*)")


def test_render_replaces_all_placeholders():
    text = "SELECT * FROM {catalog}.{schema}.workspace_reference, {catalog}.{schema}.t2"
    out = render_template(text, "main", "dbx_platform")
    assert "{catalog}" not in out and "{schema}" not in out
    assert "main.dbx_platform.workspace_reference" in out


def test_render_all_rejects_corrupted_template(tmp_path):
    from dbx_platform.dashboards import render_all

    (tmp_path / "templates").mkdir()
    for name in TEMPLATE_NAMES:
        (tmp_path / "templates" / f"{name}.lvdash.json").write_text('{"datasets": []}')
    with pytest.raises(ValueError):
        render_all(tmp_path, "m", "s")


def test_team_name_function_uses_all_tag_keys():
    sql = build_team_name_function_sql("c", "s", ["team", "project"])
    assert "c.s.team_name_from_tags" in sql
    for col in ("cluster_tags", "job_tags"):
        assert f"map_contains_key({col}, 'team')" in sql
        assert f"map_contains_key({col}, 'project')" in sql
    assert "'unknown'" in sql


def test_setup_statements_cover_all_dashboard_dependencies():
    fq_objects = "\n".join(sql for _, sql in setup_statements("c", "s", ["team"]))
    for obj in (
        "job_type_from_sku",
        "sql_type_from_sku",
        "team_name_from_tags",
        "workspace_reference",
        "warehouse_reference",
        "azure_costs",
        "azure_cost_details",
        "cost_features",
        "cost_forecasts",
        "llm_cost_daily",
        "llm_usage_hourly",
        "llm_budgets",
        "llm_source_health",
    ):
        assert f"c.s.{obj}" in fq_objects
    assert "CREATE SCHEMA" not in fq_objects


def test_direct_setup_is_disabled_without_querying(monkeypatch):
    """Schema changes run only in the deployment migration job."""
    from dbx_platform import dashboards

    query = MagicMock()
    monkeypatch.setattr(dashboards, "run_query", query)
    with pytest.raises(RuntimeError, match="schema_migrations"):
        dashboards.run_setup(None, "wh", "c", "s", ["team"])
    query.assert_not_called()


def test_wheel_entry_point_raises_on_failure(monkeypatch):
    """python_wheel_task ignores return values — only an exception fails the
    task. The console-script entry must convert exit codes into SystemExit, and
    pyproject must point at it, or every scheduled job reports SUCCESS even
    when its check crashed (the bug that hid the missing-catalog failure)."""
    import tomllib

    from dbx_platform import cli

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert pyproject["project"]["scripts"]["dbx-platform"] == "dbx_platform.cli:entry"

    monkeypatch.setattr(cli, "_dispatch", lambda argv: 1)
    with pytest.raises(SystemExit):
        cli.entry()

    monkeypatch.setattr(cli, "_dispatch", lambda argv: 0)
    assert cli.entry() is None  # success must NOT raise (or every job "fails")


def test_wheel_entry_point_carries_the_error_message(monkeypatch):
    """`bundle run` relays only the exception text, not the task's stderr — a
    bare SystemExit(1) leaves CI logs with no diagnosis (how the missing-catalog
    root cause stayed hidden behind 'SystemExit: 1'). The message must ride in
    the exception."""
    from dbx_platform import cli
    from dbx_platform.system_tables import SystemTablesUnavailableError

    def boom(argv):
        raise RuntimeError("[NO_SUCH_CATALOG_EXCEPTION] Catalog 'main' was not found")

    monkeypatch.setattr(cli, "_dispatch", boom)
    with pytest.raises(SystemExit, match="NO_SUCH_CATALOG_EXCEPTION"):
        cli.entry()

    # main() keeps its printed-message/exit-code contract for programmatic use
    assert cli.main() == 1

    def unavailable(argv):
        raise SystemTablesUnavailableError("system tables not enabled")

    monkeypatch.setattr(cli, "_dispatch", unavailable)
    assert cli.main() == 3


def test_committed_templates_are_valid_and_renderable():
    tmpl_dir = REPO_ROOT / "dashboards" / "templates"
    for name in TEMPLATE_NAMES:
        text = (tmpl_dir / f"{name}.lvdash.json").read_text()
        rendered = render_template(text, "main", "dbx_platform")
        data = json.loads(rendered)
        assert data.get("datasets") and data.get("pages"), name
        assert "{catalog}" not in rendered, name


def test_committed_rendered_dashboards_match_templates():
    dash_dir = REPO_ROOT / "dashboards"
    for name in TEMPLATE_NAMES:
        rendered_file = dash_dir / f"{name}.lvdash.json"
        assert rendered_file.exists(), (
            f"{rendered_file} missing — run: dbx-platform dashboards render"
        )
        data = json.loads(rendered_file.read_text())
        assert data.get("datasets") and data.get("pages"), name
        assert "{catalog}" not in rendered_file.read_text(), (
            f"{name} has unrendered placeholders — run: dbx-platform dashboards render"
        )


def _wheel_task_parameters():
    """Every python_wheel_task parameter list across resources/*.yml."""
    for resource_file in (REPO_ROOT / "resources").glob("*.yml"):
        data = yaml.safe_load(resource_file.read_text()) or {}
        for job in (data.get("resources", {}).get("jobs") or {}).values():
            for task in job.get("tasks") or []:
                pw = task.get("python_wheel_task")
                if pw and pw.get("parameters"):
                    yield resource_file.name, pw["parameters"]


def test_dashboard_schedule_is_health_only_and_migration_owns_setup():
    """Scheduled dashboard work reads health; only bootstrap applies DDL."""

    health_params = [
        params for _, params in _wheel_task_parameters() if params[:2] == ["dashboards", "health"]
    ]
    assert health_params
    assert not [
        params for _, params in _wheel_task_parameters() if params[:2] == ["dashboards", "setup"]
    ]
    migration_source = (REPO_ROOT / "src/dbx_platform/migrations.py").read_text()
    assert "setup_statements" in migration_source


def test_dependency_health_reports_missing_objects(monkeypatch):
    responses = [
        [{"tableName": "workspace_reference"}, {"tableName": "platform_findings"}],
        [{"function": "main.dbx_platform.job_type_from_sku"}],
    ]

    monkeypatch.setattr(
        "dbx_platform.dashboards.run_query",
        lambda *_args, **_kwargs: responses.pop(0),
    )
    rows = dependency_health(object(), "warehouse", "main", "dbx_platform")
    by_name = {row["dependency"]: row["status"] for row in rows}
    assert by_name["workspace_reference"] == "AVAILABLE"
    assert by_name["warehouse_reference"] == "MISSING"
    assert by_name["job_type_from_sku"] == "AVAILABLE"
    assert by_name["team_name_from_tags"] == "MISSING"


def test_rendered_dashboards_only_reference_objects_setup_creates():
    """Every dbx_dev.dbx_platform.<obj> a dashboard queries must be created by setup.

    Catches the class of bug where a dashboard references a helper object that
    setup_statements() does not provision (the reverse of the current failure).
    """
    created = set(
        _HELPER_OBJECT.findall(
            "\n".join(sql for _, sql in setup_statements("dbx_dev", "dbx_platform", ["team"]))
        )
    )
    for name in TEMPLATE_NAMES:
        text = (REPO_ROOT / "dashboards" / f"{name}.lvdash.json").read_text()
        referenced = set(_HELPER_OBJECT.findall(text))
        missing = referenced - created
        assert not missing, (
            f"{name} references {sorted(missing)} which dashboards setup does not "
            f"create — extend setup_statements() in src/dbx_platform/dashboards.py"
        )

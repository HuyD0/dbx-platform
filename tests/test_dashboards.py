import json
import re
from pathlib import Path

import pytest
import yaml

from dbx_platform.dashboards import (
    TEMPLATE_NAMES,
    build_team_name_function_sql,
    render_template,
    setup_statements,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Objects the dashboards reference are written as `<catalog>.<schema>.<name>`.
_HELPER_OBJECT = re.compile(r"main\.dbx_platform\.([A-Za-z_][A-Za-z0-9_]*)")


def test_render_replaces_all_placeholders():
    text = 'SELECT * FROM {catalog}.{schema}.workspace_reference, {catalog}.{schema}.t2'
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
    for obj in ("job_type_from_sku", "sql_type_from_sku", "team_name_from_tags",
                "workspace_reference", "warehouse_reference"):
        assert f"c.s.{obj}" in fq_objects


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


def test_dashboards_setup_is_wired_as_a_bundle_job():
    """The invariant: dashboards setup must be a bundle job, not CLI-only.

    Guards against the deploy-time failure where the bundle ships dashboards that
    query helper tables nothing ever provisions (the deploy workflow triggers this
    job once per prod deploy; its cron is committed paused).
    """
    setup_params = [
        params
        for _, params in _wheel_task_parameters()
        if params[:2] == ["dashboards", "setup"]
    ]
    assert setup_params, (
        "No bundle job runs `dashboards setup` — the dashboards' helper tables "
        "(main.dbx_platform.workspace_reference, etc.) would never be provisioned. "
        "Add the task to resources/dashboards_jobs.yml."
    )
    for params in setup_params:
        assert "--warehouse-id" in params, "dashboards setup job needs --warehouse-id"


def test_rendered_dashboards_only_reference_objects_setup_creates():
    """Every main.dbx_platform.<obj> a dashboard queries must be created by setup.

    Catches the class of bug where a dashboard references a helper object that
    setup_statements() does not provision (the reverse of the current failure).
    """
    created = set(
        _HELPER_OBJECT.findall(
            "\n".join(sql for _, sql in setup_statements("main", "dbx_platform", ["team"]))
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

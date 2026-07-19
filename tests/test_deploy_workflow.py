from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"


def _deploy_command() -> str:
    workflow = yaml.safe_load(DEPLOY_WORKFLOW.read_text())
    steps = workflow["jobs"]["deploy"]["steps"]
    return next(step["run"] for step in steps if step.get("name") == "Deploy")


def test_production_deploy_preserves_existing_dashboards() -> None:
    command = _deploy_command()

    assert "databricks bundle deploy -t prod" in command
    assert "--select apps.platform_console" in command
    assert "--select sql_warehouses.platform_console_warehouse" in command
    assert "--select jobs.power_controller" in command
    assert "--select jobs.schema_migrations" in command
    assert "--select dashboards." not in command
    assert "--auto-approve" not in command


def test_production_deploy_waits_for_successful_main_ci() -> None:
    workflow = yaml.safe_load(DEPLOY_WORKFLOW.read_text())

    workflow_run = workflow[True]["workflow_run"]
    deploy_job = workflow["jobs"]["deploy"]

    assert "push" not in workflow[True]
    assert workflow_run["workflows"] == ["CI"]
    assert workflow_run["types"] == ["completed"]
    assert workflow_run["branches"] == ["main"]
    assert deploy_job["if"] == (
        "${{ github.event_name == 'workflow_dispatch' "
        "|| github.event.workflow_run.conclusion == 'success' }}"
    )
    assert deploy_job["env"]["SOURCE_SHA"] == (
        "${{ github.event_name == 'workflow_run' "
        "&& github.event.workflow_run.head_sha || github.sha }}"
    )
    checkout = deploy_job["steps"][0]
    assert checkout["uses"] == "actions/checkout@v4"
    assert checkout["with"]["ref"] == "${{ env.SOURCE_SHA }}"


def test_stale_lock_recovery_is_explicit_and_manual_only() -> None:
    workflow = yaml.safe_load(DEPLOY_WORKFLOW.read_text())
    dispatch = workflow[True]["workflow_dispatch"]
    recovery = dispatch["inputs"]["recover_stale_lock"]
    deploy_step = next(
        step
        for step in workflow["jobs"]["deploy"]["steps"]
        if step.get("name") == "Deploy"
    )

    assert recovery["type"] == "boolean"
    assert recovery["default"] is False
    assert "github.event_name == 'workflow_dispatch'" in deploy_step["env"][
        "RECOVER_STALE_LOCK"
    ]
    assert "lock_args+=(--force-lock)" in deploy_step["run"]


def test_production_deploy_selects_every_declared_non_dashboard_resource() -> None:
    command = _deploy_command()
    resources: dict[str, dict[str, object]] = {}

    for resource_path in sorted((ROOT / "resources").glob("*.yml")):
        document = yaml.safe_load(resource_path.read_text()) or {}
        for resource_type, declarations in document.get("resources", {}).items():
            resources.setdefault(resource_type, {}).update(declarations or {})

    app_document = yaml.safe_load((ROOT / "resources" / "app.yml").read_text())
    for resource_type, declarations in app_document.get("resources", {}).items():
        resources.setdefault(resource_type, {}).update(declarations or {})

    selected = {
        token.removeprefix("--select ")
        for line in command.splitlines()
        if (token := line.strip().removesuffix(" \\")).startswith("--select ")
    }
    expected = {
        f"{resource_type}.{resource_name}"
        for resource_type, declarations in resources.items()
        if resource_type != "dashboards"
        for resource_name in declarations
    }

    assert selected == expected


def test_control_plane_jobs_share_one_catalog_and_schema() -> None:
    expected = [
        "--catalog",
        "${var.control_plane_catalog}",
        "--schema",
        "${var.control_plane_schema}",
    ]
    jobs = (
        ("migrations.yml", "schema_migrations"),
        ("runtime_control.yml", "power_controller"),
        ("action_executor.yml", "action_executor"),
    )

    for resource_file, job_name in jobs:
        document = yaml.safe_load((ROOT / "resources" / resource_file).read_text())
        parameters = document["resources"]["jobs"][job_name]["tasks"][0][
            "spark_python_task"
        ]["parameters"]
        start = parameters.index("--catalog")
        assert parameters[start : start + 4] == expected

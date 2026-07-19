"""Offline tests for the platform agent's pure parts.

The agent's runtime deps (langgraph, mlflow) are an optional extra, so this
loads formatting.py directly by path instead of importing the package.
"""

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "platform_agent_formatting",
    Path(__file__).resolve().parent.parent
    / "agents" / "platform_agent" / "formatting.py",
)
formatting = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(formatting)


def test_empty_rows_say_no_findings():
    assert "No findings" in formatting.rows_to_text([])


def test_rows_render_key_value_pairs():
    text = formatting.rows_to_text(
        [{"cluster_id": "c-1", "reason": "idle", "action": "terminate"}],
        tool_name="get_stale_clusters",
        source="Databricks clusters API",
    )
    assert "cluster_id=c-1" in text
    assert "action=terminate" in text
    assert "tool=get_stale_clusters" in text
    assert "source=Databricks clusters API" in text
    assert "observed_at=" in text


def test_rows_beyond_limit_are_summarized():
    rows = [{"n": i} for i in range(60)]
    text = formatting.rows_to_text(rows, limit=50)
    assert "and 10 more rows (total 60)" in text


def test_private_and_empty_fields_omitted():
    text = formatting.rows_to_text([{"a": 1, "_hidden": 2, "b": ""}])
    assert "_hidden" not in text
    assert "b=" not in text


def test_system_prompt_forbids_mutation_claims():
    assert "cannot change anything" in formatting.SYSTEM_PROMPT
    assert "--apply --yes" not in formatting.SYSTEM_PROMPT
    assert "Mission Control" in formatting.SYSTEM_PROMPT


def test_agent_formatter_masks_identity_and_pat_fields():
    text = formatting.rows_to_text(
        [
            {
                "token_id": "token-secret-id",
                "created_by": "person@example.com",
                "comment": "production emergency access",
                "action": "review",
            }
        ]
    )
    assert "token-secret-id" not in text
    assert "person@example.com" not in text
    assert "production emergency access" not in text
    assert "token_id=token-" in text
    assert "created_by=identity-" in text


def test_agent_tools_wrap_no_mutating_functions():
    """The tool module must never wrap an apply/mutate/pause/revoke function.
    The propose_* tools are dry-runs: they classify and emit a marker, and the
    mutation happens only in the Platform Console's confirm-gated apply."""
    source = (
        Path(__file__).resolve().parent.parent
        / "src" / "dbx_platform" / "platform_agent" / "tools.py"
    ).read_text()
    for forbidden in ("apply_", "pause_job", "revoke_", "run_setup", "permanent_delete",
                      "run_now", "jobs.update", "jobs.delete"):
        assert forbidden not in source, f"agent tools reference {forbidden}"


def test_platform_console_hosts_langgraph_agent_without_direct_invocation():
    root = Path(__file__).resolve().parent.parent
    runtime = (
        root / "apps" / "platform-console" / "backend" / "platform_agent.py"
    ).read_text()
    router = (
        root / "apps" / "platform-console" / "backend" / "routers" / "chat.py"
    ).read_text()
    assert "create_react_agent" in runtime
    assert "DatabricksChatModel" in runtime
    assert "databricks_langchain" not in runtime
    assert "get_platform_agent().invoke" in router
    assert "api_client.do(" not in router
    for forbidden in ("action_executor", "run_now", "jobs.update", "jobs.delete"):
        assert forbidden not in runtime


def test_system_prompt_teaches_the_proposal_convention():
    assert "ACTION_PROPOSAL" in formatting.SYSTEM_PROMPT
    assert "JOB_PROPOSAL" in formatting.SYSTEM_PROMPT
    assert "human approval" in formatting.SYSTEM_PROMPT


def test_agent_artifact_packages_local_source_instead_of_fake_pypi_requirement():
    source = (
        Path(__file__).resolve().parent.parent
        / "agents" / "platform_agent" / "deploy_agent.py"
    ).read_text()
    assert 'str(REPO_ROOT / "src")' in source
    assert '"dbx-platform",' not in source
    assert "Direct agent registration/deployment is disabled" in source

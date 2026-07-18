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
        [{"cluster_id": "c-1", "reason": "idle", "action": "terminate"}]
    )
    assert "cluster_id=c-1" in text
    assert "action=terminate" in text


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


def test_agent_tools_wrap_no_mutating_functions():
    """The tool module must never wrap an apply/mutate/pause/revoke function.
    The propose_* tools are dry-runs: they classify and emit a marker, and the
    mutation happens only in the Platform Console's confirm-gated apply."""
    source = (
        Path(__file__).resolve().parent.parent
        / "agents" / "platform_agent" / "tools.py"
    ).read_text()
    for forbidden in ("apply_", "pause_job", "revoke_", "run_setup", "permanent_delete",
                      "run_now", "jobs.update", "jobs.delete"):
        assert forbidden not in source, f"agent tools reference {forbidden}"


def test_system_prompt_teaches_the_proposal_convention():
    assert "ACTION_PROPOSAL" in formatting.SYSTEM_PROMPT
    assert "JOB_PROPOSAL" in formatting.SYSTEM_PROMPT
    assert "only after they confirm" in formatting.SYSTEM_PROMPT
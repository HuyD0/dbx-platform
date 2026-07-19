"""Pure-logic tests for the wheel-side extraction, scorers and prompt lineage."""

from __future__ import annotations

from unittest.mock import MagicMock

from dbx_platform import estimator_extract
from dbx_platform.estimator_extract import (
    EndpointToolCaller,
    aggregate_scores,
    load_eval_dataset,
    score_extraction,
)
from dbx_platform.estimator_prompts import (
    PROMPT_NAMES,
    content_hash,
    prompt_specs,
    sync_prompts,
)


def test_backend_shim_reexports_the_wheel_objects():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "platform-console"))
    from backend import estimator_extraction

    assert estimator_extraction.extract_requirements is estimator_extract.extract_requirements
    assert estimator_extraction.ExtractionError is estimator_extract.ExtractionError


# --- golden dataset -----------------------------------------------------------


def test_eval_dataset_ships_in_the_wheel_and_is_well_formed():
    dataset = load_eval_dataset()
    assert len(dataset) >= 10
    patterns = set()
    for case in dataset:
        assert case["case_id"] and case["text"]
        assert case["expected"]["pattern"]
        assert case["expected"]["monthly_requests"] >= 1
        patterns.add(case["expected"]["pattern"])
    # the dataset exercises the whole catalog shape, not one pattern
    assert {"doc_chat", "summarize", "extract_fields", "classify_route",
            "agent_workflow"} <= patterns
    # at least one adversarial case: instructions embedded in the description
    assert any("Ignore your instructions" in case["text"] for case in dataset)


# --- code scorers -------------------------------------------------------------


def test_score_extraction_tolerates_numeric_slack_and_flags_pattern():
    expected = {"pattern": "doc_chat", "monthly_requests": 4000, "corpus_gb": 3}
    good = score_extraction(
        expected, {"pattern": "doc_chat", "monthly_requests": 4400, "corpus_gb": 3.0}
    )
    assert good["pattern_match"]
    assert good["fields_within_tolerance"] == 2  # 4400 within 25% of 4000
    wrong_scale = score_extraction(
        expected, {"pattern": "doc_chat", "monthly_requests": 400_000, "corpus_gb": 3}
    )
    assert wrong_scale["fields_within_tolerance"] == 1
    wrong_pattern = score_extraction(expected, {"pattern": "summarize"})
    assert not wrong_pattern["pattern_match"]
    failed = score_extraction(expected, None)
    assert not failed["validation_passed"]
    assert failed["fields_within_tolerance"] == 0


def test_aggregate_scores_produces_bounded_metrics():
    metrics = aggregate_scores(
        [
            {"pattern_match": True, "fields_checked": 2,
             "fields_within_tolerance": 2, "validation_passed": True},
            {"pattern_match": False, "fields_checked": 2,
             "fields_within_tolerance": 1, "validation_passed": True},
        ]
    )
    assert metrics == {
        "cases": 2,
        "pattern_accuracy": 0.5,
        "field_accuracy": 0.75,
        "validation_pass_rate": 1.0,
    }
    assert aggregate_scores([])["cases"] == 0


# --- serving-endpoint tool caller ---------------------------------------------


def test_endpoint_tool_caller_forces_the_tool_and_parses_arguments():
    client = MagicMock()
    client.api_client.do.return_value = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "pick_pattern",
                                "arguments": '{"pattern": "doc_chat", "confident": true}',
                            }
                        }
                    ]
                }
            }
        ]
    }
    caller = EndpointToolCaller(client, "chat-model")
    tool = {"type": "function", "function": {"name": "pick_pattern", "parameters": {}}}
    result = caller.bind_tools([tool], tool_choice="pick_pattern").invoke(
        [{"role": "user", "content": "hi"}]
    )
    assert result.tool_calls[0]["args"] == {"pattern": "doc_chat", "confident": True}
    _method, path = client.api_client.do.call_args[0]
    assert path == "/serving-endpoints/chat-model/invocations"
    body = client.api_client.do.call_args.kwargs["body"]
    assert body["tool_choice"] == {"type": "function", "function": {"name": "pick_pattern"}}
    assert body["temperature"] == 0.0


# --- prompt lineage -----------------------------------------------------------


def test_prompt_specs_cover_both_prompts_with_stable_hashes():
    specs = prompt_specs("cat", "sch")
    assert [spec["prompt"] for spec in specs] == list(PROMPT_NAMES)
    for spec in specs:
        assert spec["registry_name"] == f"cat.sch.{spec['prompt']}"
        assert spec["content_hash"] == content_hash(spec["text"])
        assert len(spec["content_hash"]) == 12


def test_sync_prompts_registers_only_changed_content():
    specs = prompt_specs("cat", "sch")
    registered: list[tuple[str, str]] = []

    def register(name, text, digest):
        registered.append((name, digest))

    # never registered -> both register
    results = sync_prompts(specs, latest_hash=lambda name: None, register=register)
    assert [r["action"] for r in results] == ["registered", "registered"]
    assert len(registered) == 2

    # up to date -> nothing registers
    current = {spec["registry_name"]: spec["content_hash"] for spec in specs}
    registered.clear()
    results = sync_prompts(
        specs, latest_hash=lambda name: current[name], register=register
    )
    assert [r["action"] for r in results] == ["unchanged", "unchanged"]
    assert registered == []

    # one stale -> exactly that one updates
    stale = dict(current)
    stale[specs[0]["registry_name"]] = "outdated00000"
    registered.clear()
    results = sync_prompts(specs, latest_hash=lambda name: stale[name], register=register)
    assert [r["action"] for r in results] == ["updated", "unchanged"]
    assert registered == [(specs[0]["registry_name"], specs[0]["content_hash"])]
    assert results[0]["previous_hash"] == "outdated00000"


def test_trace_tagging_never_breaks_extraction(monkeypatch):
    calls = {}

    class FakeMlflow:
        @staticmethod
        def update_current_trace(tags):
            calls["tags"] = tags
            raise RuntimeError("no active trace")

    import sys

    monkeypatch.setitem(sys.modules, "mlflow", FakeMlflow)
    estimator_extract._tag_trace({"pattern": "doc_chat"})  # must not raise
    assert calls["tags"] == {"pattern": "doc_chat"}


def test_cli_new_estimator_subcommands_are_wired():
    from dbx_platform import cli

    parser = cli.build_parser()
    args = parser.parse_args(["estimator", "prompts-sync"])
    assert args.func is cli.cmd_estimator_prompts_sync
    args = parser.parse_args(
        ["estimator", "eval-extraction", "--endpoint", "chat", "--experiment", "exp-1"]
    )
    assert args.func is cli.cmd_estimator_eval_extraction
    assert args.min_pattern_accuracy == 0.8

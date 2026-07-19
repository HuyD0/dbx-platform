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


def test_prompt_specs_cover_every_prompt_with_stable_hashes():
    specs = prompt_specs("cat", "sch")
    assert [spec["prompt"] for spec in specs] == list(PROMPT_NAMES)
    assert "estimator_diagram_describe" in PROMPT_NAMES  # vision prompt registered too
    for spec in specs:
        assert spec["registry_name"] == f"cat.sch.{spec['prompt']}"
        assert spec["content_hash"] == content_hash(spec["text"])
        assert len(spec["content_hash"]) == 12


def test_sync_prompts_registers_only_changed_content():
    specs = prompt_specs("cat", "sch")
    n = len(specs)
    registered: list[tuple[str, str]] = []

    def register(name, text, digest):
        registered.append((name, digest))

    # never registered -> every prompt registers
    results = sync_prompts(specs, latest_hash=lambda name: None, register=register)
    assert [r["action"] for r in results] == ["registered"] * n
    assert len(registered) == n

    # up to date -> nothing registers
    current = {spec["registry_name"]: spec["content_hash"] for spec in specs}
    registered.clear()
    results = sync_prompts(
        specs, latest_hash=lambda name: current[name], register=register
    )
    assert [r["action"] for r in results] == ["unchanged"] * n
    assert registered == []

    # one stale -> exactly that one updates
    stale = dict(current)
    stale[specs[0]["registry_name"]] = "outdated00000"
    registered.clear()
    results = sync_prompts(specs, latest_hash=lambda name: stale[name], register=register)
    assert [r["action"] for r in results] == ["updated"] + ["unchanged"] * (n - 1)
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


# --- document text extraction -------------------------------------------------


def _pdf_bytes(text: str) -> bytes:
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    annotation = None
    try:
        from pypdf.annotations import FreeText

        annotation = FreeText(
            text=text, rect=(50, 550, 500, 650), font_size="12pt"
        )
    except Exception:  # pragma: no cover - annotation API drift
        pass
    buffer = io.BytesIO()
    if annotation is not None:
        writer.add_annotation(page_number=0, annotation=annotation)
    writer.write(buffer)
    return buffer.getvalue()


def test_text_from_document_decodes_markdown_and_text():
    from dbx_platform.estimator_extract import text_from_document

    assert "policy chat" in text_from_document("brief.md", b"# Project\npolicy chat")
    assert text_from_document("notes.TXT", "café".encode()) == "café"
    # invalid utf-8 never crashes the upload path
    assert text_from_document("notes.txt", b"\xff\xfebad") != ""


def test_text_from_document_rejects_unsupported_and_corrupt_files():
    import pytest

    from dbx_platform.estimator_extract import ExtractionError, text_from_document

    with pytest.raises(ExtractionError, match="file type is not supported"):
        text_from_document("data.xlsx", b"PK\x03\x04")
    with pytest.raises(ExtractionError, match="could not be read"):
        text_from_document("broken.pdf", b"%PDF-1.7 not really a pdf")


def test_text_from_document_reads_pdf_or_flags_empty_text():
    import pytest

    from dbx_platform.estimator_extract import ExtractionError, text_from_document

    # A text-free PDF must be a loud plain-English error, never empty text.
    with pytest.raises(ExtractionError, match="No readable text"):
        text_from_document("scan.pdf", _pdf_bytes(""))


# --- diagram / image extraction (vision path) ---------------------------------

import base64  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from dbx_platform.estimator_extract import (  # noqa: E402
    MAX_IMAGE_BYTES,
    build_describe_messages,
    describe_diagram,
    extract_from_image,
    image_data_url,
    image_mime,
)


class FakeVisionModel:
    """Fake whose unbound .invoke returns a description; bound .invoke returns
    queued tool-call args (the two-stage extraction)."""

    def __init__(self, description: str, tool_responses: list[dict]):
        self._description = description
        self._responses = list(tool_responses)
        self.saw_image = False

    def invoke(self, messages):
        # the describe call sends list content with an image_url part
        user = messages[-1]["content"]
        if isinstance(user, list):
            self.saw_image = any(p.get("type") == "image_url" for p in user)
            return SimpleNamespace(content=self._description)
        return SimpleNamespace(tool_calls=[{"args": self._responses.pop(0)}])

    def bind_tools(self, tools, *, tool_choice=None):
        return self


def test_image_mime_maps_supported_types_only():
    assert image_mime("diagram.png") == "image/png"
    assert image_mime("shot.JPG") == "image/jpeg"
    assert image_mime("flow.webp") == "image/webp"
    assert image_mime("notes.txt") is None
    assert image_mime("noext") is None


def test_image_data_url_is_base64_with_mime_prefix():
    url = image_data_url("image/png", b"\x89PNG\r\n")
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == b"\x89PNG\r\n"


def test_build_describe_messages_carries_the_image_part_and_prompt():
    messages = build_describe_messages("data:image/png;base64,AAAA")
    assert messages[0]["role"] == "system"
    parts = messages[-1]["content"]
    assert any(p["type"] == "text" for p in parts)
    image = next(p for p in parts if p["type"] == "image_url")
    assert image["image_url"]["url"] == "data:image/png;base64,AAAA"


def test_describe_diagram_reads_content_and_bounds_it():
    model = FakeVisionModel("A chat bot over policy PDFs for 200 agents.", [])
    text = describe_diagram(model, "data:image/png;base64,AAAA")
    assert "policy PDFs" in text
    assert model.saw_image


def test_describe_diagram_rejects_empty_reads():
    import pytest

    from dbx_platform.estimator_extract import ExtractionError

    with pytest.raises(ExtractionError, match="could not be read"):
        describe_diagram(FakeVisionModel("", []), "data:image/png;base64,AAAA")


def test_extract_from_image_describes_then_extracts_with_a_diagram_caveat():
    model = FakeVisionModel(
        "A document chat solution for about 4000 questions a month.",
        [
            {"pattern": "doc_chat", "confident": True},
            {"monthly_requests": 4000, "warnings": []},
        ],
    )
    requirements, warnings = extract_from_image(model, "arch.png", b"\x89PNG fake bytes")
    assert requirements["pattern"] == "doc_chat"
    assert requirements["monthly_requests"] == 4000
    assert "uploaded diagram" in warnings[0]
    assert "What was read" in warnings[0]


def test_extract_from_image_rejects_unsupported_type_and_oversize():
    import pytest

    from dbx_platform.estimator_extract import ExtractionError

    model = FakeVisionModel("x", [])
    with pytest.raises(ExtractionError, match="PNG, JPG"):
        extract_from_image(model, "notes.txt", b"data")
    with pytest.raises(ExtractionError, match="up to 5 MB"):
        extract_from_image(model, "big.png", b"x" * (MAX_IMAGE_BYTES + 1))


def test_diagram_prompt_ships_in_the_wheel():
    from dbx_platform import estimator

    text = estimator.load_prompt("estimator_diagram_describe")
    assert "diagram" in text.lower()

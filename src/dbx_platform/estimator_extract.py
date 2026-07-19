"""Structured requirements extraction for the AI Cost Planner.

The only place an AI model touches the estimator. Two forced tool calls with
progressive disclosure keep context small and the prompt prefix byte-stable
(static instructions + compact catalog first, volatile user text last, so the
serving endpoint's prompt cache applies):

1. ``classify_pattern`` — pattern ids + one-line labels only.
2. ``extract_requirements`` — only the chosen pattern's field guide.

The model never does arithmetic and its output never reaches the cost engine
directly: everything is validated by the wheel's ``validate_requirements``
(one corrective retry, then a client error) and then shown to a person for
review/edit before any estimate is computed. User text is bounded and treated
as untrusted data, mirroring the chat router's PAGE_CONTEXT rules. Prompt
texts ship in the wheel (``estimator_data/prompts/``) and are the same bytes
the deploy path registers to the MLflow prompt registry.
"""

from __future__ import annotations

import hashlib
import json
from importlib import resources

from dbx_platform import estimator

MAX_TEXT_CHARS = 8_000


class ExtractionError(ValueError):
    """The model could not produce a valid requirements document."""


def prompt_version(name: str) -> str:
    """Content hash identifying the registered prompt version in trace tags."""

    return hashlib.sha256(estimator.load_prompt(name).encode("utf-8")).hexdigest()[:12]


def bound_text(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        raise ExtractionError("Describe what you want the system to do first.")
    return cleaned[:MAX_TEXT_CHARS]


def catalog_summary(patterns: dict) -> str:
    """Compact, stable pattern catalog for stage 1. Pure."""

    return "\n".join(
        f"- {key}: {value['label']} — {value['description']}"
        for key, value in sorted(patterns["patterns"].items())
    )


def build_pick_pattern_tool(patterns: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "pick_pattern",
            "description": "Record the best-matching solution pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "enum": sorted(patterns["patterns"]),
                        "description": "The catalog pattern id that best matches.",
                    },
                    "confident": {
                        "type": "boolean",
                        "description": "False when nothing fits well.",
                    },
                },
                "required": ["pattern", "confident"],
            },
        },
    }


_FIELD_DESCRIPTIONS = {
    "monthly_requests": "How many requests per month in production, converted "
    "faithfully from whatever the description states.",
    "avg_input_tokens": "Only when the description sizes a typical request; 0 accepts "
    "the pattern default.",
    "avg_output_tokens": "Only when the description sizes a typical answer; 0 accepts "
    "the pattern default.",
    "corpus_gb": "Document collection size in GB, only if stated; 0 accepts the "
    "pattern default.",
    "corpus_growth_pct_monthly": "Monthly growth of the document collection in percent.",
    "agent_steps": "How many steps the assistant takes per task, only if described; "
    "0 accepts the pattern default.",
    "peak_rps": "Peak requests per second, only if stated; 0 derives it from traffic.",
    "needs_memory": "Whether it must remember each user between sessions, only if "
    "the description says so.",
    "monthly_active_users": "How many people use it each month, only if stated.",
    "region": "Cloud region, only if the description names one.",
    "currency": "Currency code, only if the description names one.",
}


def build_extraction_tool(patterns: dict, pattern_id: str) -> dict:
    """The record_requirements tool: schema mirrors the wheel's Requirements."""

    defaults = patterns["patterns"][pattern_id]["defaults"]
    properties: dict = {
        "monthly_requests": {"type": "integer", "minimum": 1},
        "avg_input_tokens": {"type": "integer", "minimum": 0},
        "avg_output_tokens": {"type": "integer", "minimum": 0},
        "corpus_gb": {"type": "number", "minimum": 0},
        "corpus_growth_pct_monthly": {"type": "number", "minimum": 0, "maximum": 100},
        "agent_steps": {"type": "integer", "minimum": 0, "maximum": 50},
        "peak_rps": {"type": "number", "minimum": 0},
        "needs_memory": {"type": "boolean"},
        "monthly_active_users": {"type": "integer", "minimum": 0},
        "region": {"type": "string"},
        "currency": {"type": "string"},
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Every conversion, assumption or ambiguity you noticed, "
            "in plain English, for the human reviewer.",
        },
    }
    for name, description in _FIELD_DESCRIPTIONS.items():
        properties[name]["description"] = description
    return {
        "type": "function",
        "function": {
            "name": "record_requirements",
            "description": (
                f"Record sizing facts for the '{pattern_id}' pattern. Pattern "
                f"defaults (used when a field is 0/unset): {json.dumps(defaults)}"
            ),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": ["monthly_requests", "warnings"],
            },
        },
    }


def _forced_tool_call(model, tool: dict, messages: list[dict]) -> dict:
    bound = model.bind_tools([tool], tool_choice=tool["function"]["name"])
    result = bound.invoke(messages)
    calls = getattr(result, "tool_calls", None) or []
    if not calls:
        raise ExtractionError("The AI model did not return a structured answer.")
    return dict(calls[0].get("args") or {})


def classify_pattern(model, text: str, patterns: dict | None = None) -> tuple[str, bool]:
    """Stage 1: pick the pattern from a compact catalog. Static prefix first."""

    patterns = patterns or estimator.load_patterns()
    args = _forced_tool_call(
        model,
        build_pick_pattern_tool(patterns),
        [
            {"role": "system", "content": estimator.load_prompt("estimator_pattern_classify")},
            {"role": "system", "content": f"Pattern catalog:\n{catalog_summary(patterns)}"},
            {
                "role": "user",
                "content": f"<user_description>\n{bound_text(text)}\n</user_description>",
            },
        ],
    )
    pattern = str(args.get("pattern") or "")
    if pattern not in patterns["patterns"]:
        raise ExtractionError("The AI model chose an unknown solution pattern.")
    return pattern, bool(args.get("confident", False))


def extract_requirements(
    model, text: str, patterns: dict | None = None
) -> tuple[dict, list[str]]:
    """Two-stage extraction returning (validated requirements dict, warnings).

    Never trusted blindly: the result feeds the editable review screen, not
    the engine. One corrective retry on validation failure, then the error
    surfaces to the client.
    """
    patterns = patterns or estimator.load_patterns()
    pattern, confident = classify_pattern(model, text, patterns)
    tool = build_extraction_tool(patterns, pattern)
    messages = [
        {"role": "system", "content": estimator.load_prompt("estimator_requirements_extract")},
        {
            "role": "system",
            "content": f"Chosen pattern: {pattern} "
            f"({patterns['patterns'][pattern]['label']})",
        },
        {
            "role": "user",
            "content": f"<user_description>\n{bound_text(text)}\n</user_description>",
        },
    ]
    args = _forced_tool_call(model, tool, messages)
    warnings = [str(w) for w in (args.pop("warnings", None) or [])][:20]
    if not confident:
        warnings.insert(
            0,
            "The pattern match was uncertain — double-check the chosen solution "
            "pattern before trusting the numbers.",
        )
    raw = {"pattern": pattern, **args}
    try:
        requirements = estimator.validate_requirements(raw, patterns)
    except ValueError as first_error:
        messages.append(
            {
                "role": "system",
                "content": f"Your previous answer was rejected: {first_error} "
                "Call record_requirements again with corrected values.",
            }
        )
        args = _forced_tool_call(model, tool, messages)
        warnings.extend(str(w) for w in (args.pop("warnings", None) or [])[:5])
        try:
            requirements = estimator.validate_requirements(
                {"pattern": pattern, **args}, patterns
            )
        except ValueError as second_error:
            raise ExtractionError(str(second_error)) from second_error
    from dataclasses import asdict

    _tag_trace(
        {
            "prompt_classify_version": prompt_version("estimator_pattern_classify"),
            "prompt_extract_version": prompt_version("estimator_requirements_extract"),
            "engine_version": estimator.ENGINE_VERSION,
            "rate_card_version": estimator.load_rate_card().get("version", ""),
            "pattern": pattern,
        }
    )
    return asdict(requirements), warnings


def _tag_trace(tags: dict[str, str]) -> None:
    """Best-effort MLflow trace tags — lineage must never break extraction."""

    try:
        import mlflow

        mlflow.update_current_trace(tags=tags)
    except Exception:  # noqa: BLE001 - mlflow absent or no active trace
        return


# --- serving-endpoint tool caller (databricks-sdk only, for the eval job) -----


class EndpointToolCaller:
    """Minimal forced-tool-call client over a serving endpoint.

    Duck-types the two methods the extraction flow uses (``bind_tools`` /
    ``invoke``) without LangChain, so the eval job can drive the exact
    production extraction path with only the databricks-sdk the wheel already
    depends on.
    """

    def __init__(self, workspace_client, endpoint: str, *, max_tokens: int = 1200):
        self._client = workspace_client
        self._endpoint = endpoint
        self._max_tokens = max_tokens
        self._tools: list[dict] = []
        self._tool_choice: str | None = None

    def bind_tools(self, tools: list[dict], *, tool_choice: str | None = None):
        bound = EndpointToolCaller(
            self._client, self._endpoint, max_tokens=self._max_tokens
        )
        bound._tools = list(tools)
        bound._tool_choice = tool_choice
        return bound

    def invoke(self, messages: list[dict]):
        from types import SimpleNamespace
        from urllib.parse import quote

        payload: dict = {
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": self._max_tokens,
            "tools": self._tools,
        }
        if self._tool_choice:
            payload["tool_choice"] = {
                "type": "function",
                "function": {"name": self._tool_choice},
            }
        response = self._client.api_client.do(
            "POST",
            f"/serving-endpoints/{quote(self._endpoint)}/invocations",
            body=payload,
        )
        calls = []
        for choice in response.get("choices") or []:
            for call in (choice.get("message") or {}).get("tool_calls") or []:
                arguments = (call.get("function") or {}).get("arguments") or "{}"
                try:
                    calls.append({"args": json.loads(arguments)})
                except ValueError:
                    continue
        return SimpleNamespace(tool_calls=calls)


# --- extraction eval (golden dataset + pure code scorers) ---------------------


def load_eval_dataset() -> list[dict]:
    """Golden extraction cases shipped in the wheel (no repo checkout in jobs)."""

    text = (
        resources.files("dbx_platform.estimator_data")
        .joinpath("extraction_eval.jsonl")
        .read_text("utf-8")
    )
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def score_extraction(
    expected: dict, actual: dict | None, *, tolerance_pct: float = 25.0
) -> dict:
    """Pure code scorer for one eval case — no AI judges needed here.

    Numeric fields pass when within ``tolerance_pct`` of the expected value;
    everything else is exact. ``actual=None`` (extraction failed) fails all.
    """
    if actual is None:
        return {
            "pattern_match": False,
            "fields_checked": max(len(expected) - 1, 0),
            "fields_within_tolerance": 0,
            "validation_passed": False,
        }
    checked = 0
    within = 0
    for field, want in expected.items():
        if field == "pattern":
            continue
        checked += 1
        got = actual.get(field)
        if isinstance(want, (int, float)) and not isinstance(want, bool):
            if got is None:
                continue
            allowed = abs(float(want)) * tolerance_pct / 100
            if abs(float(got) - float(want)) <= max(allowed, 0.5):
                within += 1
        elif got == want:
            within += 1
    return {
        "pattern_match": actual.get("pattern") == expected.get("pattern"),
        "fields_checked": checked,
        "fields_within_tolerance": within,
        "validation_passed": True,
    }


def aggregate_scores(scores: list[dict]) -> dict:
    """Aggregate per-case scores into the metrics logged to MLflow. Pure."""

    cases = len(scores)
    if not cases:
        return {
            "cases": 0, "pattern_accuracy": 0.0,
            "field_accuracy": 0.0, "validation_pass_rate": 0.0,
        }
    checked = sum(score["fields_checked"] for score in scores)
    return {
        "cases": cases,
        "pattern_accuracy": round(
            sum(1 for s in scores if s["pattern_match"]) / cases, 4
        ),
        "field_accuracy": round(
            (sum(s["fields_within_tolerance"] for s in scores) / checked)
            if checked else 1.0,
            4,
        ),
        "validation_pass_rate": round(
            sum(1 for s in scores if s["validation_passed"]) / cases, 4
        ),
    }


# --- document text extraction (upload path) -----------------------------------

MAX_PDF_PAGES = 50


def text_from_document(filename: str, data: bytes) -> str:
    """Plain text from an uploaded document, ready for ``bound_text``.

    PDF needs the console's ``pypdf`` (lazy import — the wheel itself stays
    SDK-only); Markdown and plain text decode directly. Anything else gets a
    plain-English rejection. Images/diagrams are deliberately unsupported
    until a multimodal endpoint exists.
    """
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix in ("md", "markdown", "txt"):
        return data.decode("utf-8", errors="replace")
    if suffix == "pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover - console always ships pypdf
            raise ExtractionError(
                "PDF reading is not available in this installation."
            ) from exc
        import io

        try:
            reader = PdfReader(io.BytesIO(data))
        except Exception as exc:  # noqa: BLE001 - corrupt/encrypted uploads
            raise ExtractionError(
                "That PDF could not be read - it may be corrupted or "
                "password-protected."
            ) from exc
        pages = reader.pages[:MAX_PDF_PAGES]
        text = "\n".join((page.extract_text() or "") for page in pages).strip()
        if not text:
            raise ExtractionError(
                "No readable text was found in that PDF. Scanned images are "
                "not supported yet - paste the key details as text instead."
            )
        return text
    raise ExtractionError(
        "That file type is not supported. Upload a PDF, Markdown or text "
        "document, an architecture diagram image, or use the text box."
    )


# --- diagram / image extraction (vision path) ---------------------------------

# Claude's per-image base64 limit; fail early with a plain message rather than
# let the serving endpoint reject an oversized image.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
SUPPORTED_IMAGE_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}


def image_mime(filename: str) -> str | None:
    """Mime type for a supported image upload, else None. Pure."""

    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return SUPPORTED_IMAGE_TYPES.get(suffix)


def image_data_url(mime_type: str, data: bytes) -> str:
    """OpenAI-style base64 data URL for an image. Pure."""

    import base64

    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"


def build_describe_messages(data_url: str) -> list[dict]:
    """Vision prompt asking the model to describe the diagram in plain English.

    The user content is a list of parts (text + image_url); the chat adapter
    passes list content through untouched, and the bound endpoint
    (Claude Sonnet) is vision-capable.
    """
    return [
        {"role": "system", "content": estimator.load_prompt("estimator_diagram_describe")},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Describe the AI solution shown in this diagram.",
                },
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]


def _message_text(result) -> str:
    """Read plain text from a chat result whose content may be parts."""

    content = getattr(result, "content", "")
    if isinstance(content, list):
        return " ".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        ).strip()
    return str(content or "").strip()


def describe_diagram(model, data_url: str) -> str:
    """One vision call: diagram → bounded plain-English description."""

    result = model.invoke(build_describe_messages(data_url))
    text = _message_text(result)
    if not text:
        raise ExtractionError(
            "The diagram could not be read. Add a clearer image or describe "
            "the solution in the text box instead."
        )
    return bound_text(text)


def extract_from_image(model, filename: str, data: bytes) -> tuple[dict, list[str]]:
    """Diagram image → validated requirements, via describe-then-extract.

    The image is turned into plain-English text by a vision call, then run
    through the same two-stage extraction as any other text — so the result
    still lands on the human review screen, never straight into the engine.
    """
    mime = image_mime(filename)
    if mime is None:
        raise ExtractionError(
            "Only PNG, JPG, WEBP and GIF diagrams are supported."
        )
    if len(data) > MAX_IMAGE_BYTES:
        raise ExtractionError(
            "Diagram images up to 5 MB are supported - export a smaller image."
        )
    description = describe_diagram(model, image_data_url(mime, data))
    requirements, warnings = extract_requirements(model, description)
    snippet = description[:200] + ("…" if len(description) > 200 else "")
    warnings.insert(
        0,
        "Read from an uploaded diagram, so double-check the interpreted "
        f"requirements before trusting the numbers. What was read: “{snippet}”",
    )
    _tag_trace(
        {
            "prompt_describe_version": prompt_version("estimator_diagram_describe"),
            "source": "diagram",
        }
    )
    return requirements, warnings

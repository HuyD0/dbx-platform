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

    return asdict(requirements), warnings

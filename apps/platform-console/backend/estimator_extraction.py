"""Compatibility shim: the extraction logic lives in the dbx-platform wheel.

``dbx_platform.estimator_extract`` is the single source of truth so the CLI
eval harness and this app drive the exact same two-stage extraction path.
This module survives as the backend-facing import site (and monkeypatch
target in tests); it must hold no logic of its own.
"""

from __future__ import annotations

from dbx_platform.estimator_extract import (
    MAX_IMAGE_BYTES,
    MAX_TEXT_CHARS,
    ExtractionError,
    bound_text,
    build_extraction_tool,
    build_pick_pattern_tool,
    catalog_summary,
    classify_pattern,
    extract_from_image,
    extract_requirements,
    image_mime,
    prompt_version,
)

__all__ = [
    "MAX_IMAGE_BYTES",
    "MAX_TEXT_CHARS",
    "ExtractionError",
    "bound_text",
    "build_extraction_tool",
    "build_pick_pattern_tool",
    "catalog_summary",
    "classify_pattern",
    "extract_from_image",
    "extract_requirements",
    "image_mime",
    "prompt_version",
]

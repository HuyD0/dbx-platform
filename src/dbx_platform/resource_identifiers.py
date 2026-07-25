"""Canonical resource-identifier extraction for evidence correlation.

Action targets and finding evidence intentionally use small, heterogeneous
resource objects.  Correlation is exact after normalizing scalar values to
trimmed strings; it never uses display text similarity or partial matching.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

RESOURCE_ID_FIELDS = frozenset(
    {
        "resource_id",
        "resource_key",
        "id",
        "cluster_id",
        "job_id",
        "token_id",
        "policy_id",
        "warehouse_id",
        "endpoint_id",
        "endpoint_name",
        "app_id",
        "app_name",
        "budget_id",
        "schedule_id",
        "job_key",
        "name",
    }
)


def _normalized_scalar(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (str, int, float)):
        normalized = str(value).strip()
        return normalized or None
    return None


def extract_resource_ids(value: Any) -> set[str]:
    """Extract exact identifiers from nested target or resource structures."""

    identifiers: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = str(key).strip().lower()
            if normalized_key in RESOURCE_ID_FIELDS:
                identifier = _normalized_scalar(item)
                if identifier is not None:
                    identifiers.add(identifier)
            if isinstance(item, (Mapping, list, tuple)):
                identifiers.update(extract_resource_ids(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, (Mapping, list, tuple)):
                identifiers.update(extract_resource_ids(item))
            else:
                identifier = _normalized_scalar(item)
                if identifier is not None:
                    identifiers.add(identifier)
    else:
        identifier = _normalized_scalar(value)
        if identifier is not None:
            identifiers.add(identifier)
    return identifiers


def parse_resource_ids(value: Any) -> set[str]:
    """Extract identifiers from an object or a JSON-encoded resource value."""

    if value is None or value == "":
        return set()
    if not isinstance(value, str):
        return extract_resource_ids(value)
    text = value.strip()
    if not text:
        return set()
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return {text}
    return extract_resource_ids(decoded)


def matching_resource_ids(left: Any, right: Any) -> set[str]:
    """Return identifiers shared by two canonical resource representations."""

    return extract_resource_ids(left).intersection(parse_resource_ids(right))

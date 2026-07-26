"""Typed, presentation-ready views over the unified AI catalog.

The persisted catalog remains the audit source of truth.  This module adds the
pure classification, filtering, faceting, and cursor pagination used by the
Platform Console and assistant without changing the underlying snapshots.
"""

from __future__ import annotations

import base64
import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any, Literal

InventoryView = Literal["managed_or_risky", "customer_managed", "all"]

_BROAD_PRINCIPALS = {
    "account users",
    "all users",
    "users",
    "workspace users",
}
_HEALTHY_STATUSES = {"available", "healthy", "success"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fold(value: Any) -> str:
    return _text(value).casefold()


def _details(row: dict) -> dict:
    value = row.get("details_json")
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _tags(row: dict) -> dict[str, str]:
    details = _details(row)
    result: dict[str, str] = {}
    for key in ("account_tags", "tags"):
        raw = details.get(key)
        if not isinstance(raw, dict):
            continue
        for tag_key, tag_value in raw.items():
            if tag_value not in (None, ""):
                result[str(tag_key)] = str(tag_value)
    return result


def access_lookup_keys(row: dict) -> tuple[str, ...]:
    """Return the exact entity key plus any governed parent access scope."""

    key = _text(row.get("model_key"))
    source = _fold(row.get("source"))
    keys = [key] if key else []
    if source == "azure_openai" and "/deployments/" in key.casefold():
        keys.append(key.rsplit("/deployments/", 1)[0])
    elif source == "databricks_serving" and "/" in key:
        keys.append(key.split("/", 1)[0])
    return tuple(dict.fromkeys(keys))


def is_system_model(row: dict) -> bool:
    """Return whether a catalog row is a Databricks-provided system model."""

    if _fold(row.get("source")) != "databricks_uc":
        return False
    identifiers = (
        row.get("model_key"),
        row.get("model_name"),
        row.get("resource_id"),
    )
    return any(
        value.startswith("system.ai.") or value.startswith("uc:system.ai.")
        for value in map(_fold, identifiers)
    )


def _group(row: dict, *, system_model: bool) -> dict[str, str]:
    source = _fold(row.get("source"))
    if system_model:
        return {"key": "databricks:system-models", "label": "Databricks system models"}
    if source == "azure_openai":
        resource_id = _text(row.get("resource_id"))
        account_id = resource_id.casefold().split("/deployments/", 1)[0]
        label = _text(row.get("endpoint_name")) or _text(row.get("resource_group"))
        return {
            "key": f"azure:{account_id or label.casefold()}",
            "label": label or "Azure AI account",
        }
    if source == "databricks_serving":
        endpoint = _text(row.get("endpoint_name")) or _text(row.get("model_name"))
        return {
            "key": f"serving:{endpoint.casefold()}",
            "label": endpoint or "Serving endpoint",
        }
    model_name = _text(row.get("model_name"))
    parts = model_name.split(".")
    namespace = ".".join(parts[:2]) if len(parts) >= 3 else "Unity Catalog"
    return {
        "key": f"uc:{namespace.casefold()}",
        "label": namespace,
    }


def _risk_reasons(row: dict, access_rows: Iterable[dict]) -> list[str]:
    reasons: list[str] = []
    if row.get("key_auth_enabled") is True:
        reasons.append("Key authentication enabled")
    status = _fold(row.get("status"))
    if status and not any(
        marker in status for marker in ("ready", "running", "succeeded", "active")
    ):
        reasons.append(f"Status: {_text(row.get('status'))}")
    if not _text(row.get("owner")) and not is_system_model(row):
        reasons.append("Owner missing")
    if any(_fold(access.get("principal_name")) in _BROAD_PRINCIPALS for access in access_rows):
        reasons.append("Broad user access")
    return reasons


def normalize_inventory(
    catalog_rows: list[dict],
    access_rows: list[dict],
) -> list[dict]:
    """Enrich raw catalog rows for an application-facing inventory."""

    access_by_model: dict[str, list[dict]] = defaultdict(list)
    for row in access_rows:
        access_by_model[_text(row.get("model_key"))].append(row)

    entities: list[dict] = []
    for raw in catalog_rows:
        row = dict(raw)
        model_key = _text(row.get("model_key"))
        model_access = [
            access
            for key in access_lookup_keys(row)
            for access in access_by_model.get(key, [])
        ]
        system_model = is_system_model(row)
        risks = _risk_reasons(row, model_access)
        exposures = []
        if row.get("key_auth_enabled") is True:
            exposures.append("key_auth")
        if any(
            _fold(access.get("principal_name")) in _BROAD_PRINCIPALS
            for access in model_access
        ):
            exposures.append("broad_access")
        group = _group(row, system_model=system_model)
        entities.append(
            {
                **row,
                "model_key": model_key,
                "display_name": (
                    _text(row.get("model_name"))
                    or _text(row.get("endpoint_name"))
                    or model_key
                ),
                "ownership": "system" if system_model else "customer_managed",
                "needs_attention": bool(risks),
                "risk": "attention" if risks else "clear",
                "risk_reasons": risks,
                "exposure": exposures or ["none_attested"],
                "access_count": len(model_access),
                "tags": _tags(row),
                "group_key": group["key"],
                "group_label": group["label"],
            }
        )
    return entities


def normalize_source_health(rows: list[dict]) -> list[dict]:
    """Expose only AI inventory source checks with explicit truncation."""

    result: list[dict] = []
    for row in rows:
        source_key = _text(row.get("source_key"))
        if _fold(row.get("source_type")) != "inventory" and not source_key.startswith(
            "ai-catalog-"
        ):
            continue
        notes = _text(row.get("notes"))
        status = _fold(row.get("status")) or "unknown"
        result.append(
            {
                "source_key": source_key,
                "source": _text(row.get("source")) or source_key,
                "status": "healthy" if status in _HEALTHY_STATUSES else status,
                "row_count": int(row.get("row_count") or 0),
                "freshness": row.get("freshness"),
                "checked_at": row.get("checked_at"),
                "last_success_at": row.get("last_success_at"),
                "notes": notes or None,
                "truncated": "truncat" in notes.casefold() or "capped" in notes.casefold(),
            }
        )
    return result


def _cursor(offset: int) -> str:
    raw = f"inventory:{offset}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = base64.urlsafe_b64decode(padded.encode()).decode()
        prefix, raw_offset = value.split(":", 1)
        offset = int(raw_offset)
    except (TypeError, ValueError) as exc:
        raise ValueError("cursor is invalid") from exc
    if prefix != "inventory" or offset < 0:
        raise ValueError("cursor is invalid")
    return offset


def _matches(
    row: dict,
    *,
    query: str,
    source: str | None,
    provider: str | None,
    status: str | None,
    entity_type: str | None,
    environment: str | None,
    owner: str | None,
    exposure: str | None,
    risk: str | None,
    view: InventoryView,
) -> bool:
    if view == "customer_managed" and row["ownership"] != "customer_managed":
        return False
    if (
        view == "managed_or_risky"
        and row["ownership"] != "customer_managed"
        and not row["needs_attention"]
    ):
        return False
    for expected, actual in (
        (source, row.get("source")),
        (provider, row.get("provider")),
        (status, row.get("status")),
        (entity_type, row.get("entity_type")),
        (environment, row.get("environment")),
        (owner, row.get("owner")),
        (risk, row.get("risk")),
    ):
        if expected and _fold(expected) != _fold(actual):
            return False
    if exposure and _fold(exposure) not in {
        _fold(value) for value in row.get("exposure") or []
    }:
        return False
    needle = _fold(query)
    if not needle:
        return True
    haystack = " ".join(
        _text(value)
        for value in (
            row.get("model_key"),
            row.get("display_name"),
            row.get("endpoint_name"),
            row.get("provider"),
            row.get("owner"),
            row.get("resource_group"),
            row.get("group_label"),
            json.dumps(row.get("tags") or {}, sort_keys=True),
        )
    ).casefold()
    return needle in haystack


def _facets(rows: list[dict]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for name in (
        "source",
        "provider",
        "environment",
        "owner",
        "status",
        "entity_type",
        "ownership",
        "risk",
    ):
        counts = Counter(_text(row.get(name)) or "unknown" for row in rows)
        result[name] = [
            {"value": value, "count": count}
            for value, count in sorted(counts.items(), key=lambda item: item[0].casefold())
        ]
    exposure_counts = Counter(
        _text(value) or "unknown"
        for row in rows
        for value in (row.get("exposure") or ["unknown"])
    )
    result["exposure"] = [
        {"value": value, "count": count}
        for value, count in sorted(
            exposure_counts.items(), key=lambda item: item[0].casefold()
        )
    ]
    return result


def build_inventory_page(
    catalog_rows: list[dict],
    access_rows: list[dict],
    source_health_rows: list[dict],
    *,
    query: str = "",
    source: str | None = None,
    provider: str | None = None,
    status: str | None = None,
    entity_type: str | None = None,
    environment: str | None = None,
    owner: str | None = None,
    exposure: str | None = None,
    risk: str | None = None,
    view: InventoryView = "managed_or_risky",
    cursor: str | None = None,
    limit: int = 50,
    export: bool = False,
) -> dict:
    """Build a stable, paginated inventory response from current snapshots."""

    if view not in {"managed_or_risky", "customer_managed", "all"}:
        raise ValueError("view must be managed_or_risky, customer_managed, or all")
    page_limit = max(1, min(int(limit), 200))
    entities = normalize_inventory(catalog_rows, access_rows)
    filtered = [
        row
        for row in entities
        if _matches(
            row,
            query=query,
            source=source,
            provider=provider,
            status=status,
            entity_type=entity_type,
            environment=environment,
            owner=owner,
            exposure=exposure,
            risk=risk,
            view=view,
        )
    ]
    filtered.sort(
        key=lambda row: (
            not bool(row["needs_attention"]),
            _fold(row.get("group_label")),
            _fold(row.get("display_name")),
            _fold(row.get("model_key")),
        )
    )
    start = 0 if export else _offset(cursor)
    page = filtered if export else filtered[start : start + page_limit]
    next_cursor = None
    if not export and start + page_limit < len(filtered):
        next_cursor = _cursor(start + page_limit)
    groups = Counter(_text(row.get("group_key")) for row in page)
    return {
        "items": page,
        "total": len(filtered),
        "next_cursor": next_cursor,
        "facets": _facets(entities),
        "summary": {
            "total": len(entities),
            "customer_managed": sum(
                row["ownership"] == "customer_managed" for row in entities
            ),
            "system": sum(row["ownership"] == "system" for row in entities),
            "needs_attention": sum(bool(row["needs_attention"]) for row in entities),
            "key_auth_exposed": sum(
                row.get("key_auth_enabled") is True for row in entities
            ),
            "groups_on_page": len(groups),
        },
        "source_health": normalize_source_health(source_health_rows),
    }


def build_inventory_detail(
    model_key: str,
    catalog_rows: list[dict],
    access_rows: list[dict],
    source_health_rows: list[dict],
) -> dict | None:
    """Return one inventory entity with access and applicable source health."""

    entities = normalize_inventory(catalog_rows, access_rows)
    entity = next(
        (row for row in entities if _text(row.get("model_key")) == model_key),
        None,
    )
    if entity is None:
        return None
    access = [
        dict(row)
        for row in access_rows
        if _text(row.get("model_key")) in access_lookup_keys(entity)
    ]
    source = _text(entity.get("source"))
    health = [
        row
        for row in normalize_source_health(source_health_rows)
        if source.replace("_", "-") in _fold(row.get("source_key"))
        or source.split("_", 1)[0] in _fold(row.get("source"))
    ]
    return {
        "entity": entity,
        "access": access,
        "source_health": health,
    }

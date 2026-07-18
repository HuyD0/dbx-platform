"""LLM Cost & Value API backed only by the persisted canonical ledger.

Feature detection and provider queries belong to the scheduled rollup. Web
requests read the platform-owned ledger, source-health and budget tables so a
page load never reaches into preview system tables or Azure billing directly.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from backend import cache, deps
from backend.models import envelope
from dbx_platform import llm_cost

router = APIRouter(prefix="/api/llm-cost")


def _dependency_health(source: str, error: Exception) -> dict[str, Any]:
    """Return a safe dependency status without exposing SQL or identifiers."""

    return llm_cost.coverage_record(
        source,
        "unavailable",
        freshness="unknown",
        retention_days=None,
        notes=(
            "Persisted Mission Control data is unavailable; run the schema "
            f"migration and governed LLM rollup ({error.__class__.__name__})."
        ),
    )


def _ledger(days: int, refresh: bool = False) -> tuple[dict, Any, bool]:
    days = deps.clamp_days(days, 1, 400)
    workspace_id, environment = deps.control_plane_scope()

    def load() -> dict:
        w = deps.get_ws()
        warehouse = deps.warehouse_id()
        settings = deps.get_settings()
        catalog = settings.dashboard_catalog
        schema = settings.dashboard_schema
        coverage: list[dict] = []

        try:
            costs = llm_cost.read_llm_cost_daily(
                w,
                warehouse,
                catalog,
                schema,
                workspace_id,
                environment,
                days,
            )
        except Exception as error:  # noqa: BLE001 - dependency health is explicit
            costs = []
            coverage.append(_dependency_health("Persisted LLM cost ledger", error))

        try:
            usage = llm_cost.read_llm_usage_hourly(
                w,
                warehouse,
                catalog,
                schema,
                workspace_id,
                environment,
                min(days, 90),
            )
        except Exception as error:  # noqa: BLE001 - dependency health is explicit
            usage = []
            coverage.append(_dependency_health("Persisted LLM usage ledger", error))

        try:
            source_health = llm_cost.read_llm_source_health(
                w,
                warehouse,
                catalog,
                schema,
                workspace_id,
                environment,
            )
            if source_health:
                coverage.extend(source_health)
            else:
                coverage.append(
                    llm_cost.coverage_record(
                        "LLM rollup source health",
                        "unavailable",
                        freshness="never",
                        retention_days=None,
                        notes=(
                            "No source-health snapshot exists for this workspace "
                            "and environment; run the governed LLM rollup."
                        ),
                    )
                )
        except Exception as error:  # noqa: BLE001 - migration may be pending
            coverage.append(_dependency_health("LLM rollup source health", error))

        try:
            configured = llm_cost.budget_rows(
                w,
                warehouse,
                catalog,
                schema,
                workspace_id,
                environment,
            )
            budgets = llm_cost.evaluate_budgets(configured, costs)
            coverage.append(
                llm_cost.coverage_record(
                    "LLM budgets",
                    "available",
                    freshness="persisted current-month configuration",
                    retention_days=None,
                    notes="Thresholds default to 80% warning and 100% critical",
                )
            )
        except Exception as error:  # noqa: BLE001 - migration may be pending
            budgets = []
            coverage.append(_dependency_health("LLM budgets", error))

        return {
            "cost_rows": costs,
            "usage_rows": usage,
            "coverage": coverage,
            "budgets": budgets,
        }

    key = f"llm-cost/ledger/{workspace_id}/{environment}/{days}"
    return cache.cached(key, load, refresh)


@router.get("/summary")
def summary(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days, 1, 400)
    # MTD comparison needs the same elapsed slice of the prior month, which
    # can span 62 calendar days at month end.
    ledger, as_of, hit = _ledger(max(days, 62), refresh)
    data = llm_cost.summarize(ledger["cost_rows"], ledger["usage_rows"], days)
    data["coverage"] = ledger["coverage"]
    data["budgets"] = ledger["budgets"]
    return envelope(data, as_of, hit)


@router.get("/timeseries")
def timeseries(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days, 1, 400)
    ledger, as_of, hit = _ledger(days, refresh)
    data = llm_cost.time_series(ledger["cost_rows"], ledger["usage_rows"])
    return envelope(data, as_of, hit)


@router.get("/breakdown")
def cost_breakdown(dimension: str = "all", days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days, 1, 400)
    ledger, as_of, hit = _ledger(days, refresh)
    dimensions = sorted(llm_cost.BREAKDOWN_DIMENSIONS) if dimension == "all" else [dimension]
    data = [
        row
        for selected in dimensions
        for row in llm_cost.breakdown(ledger["cost_rows"], ledger["usage_rows"], selected)
    ]
    return envelope(data, as_of, hit)


@router.get("/efficiency")
def cost_efficiency(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days, 1, 400)
    ledger, as_of, hit = _ledger(days, refresh)
    data = llm_cost.efficiency(ledger["cost_rows"], ledger["usage_rows"])
    return envelope(data, as_of, hit)


@router.get("/data-health")
def data_health(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days, 1, 400)
    ledger, as_of, hit = _ledger(days, refresh)
    data = {
        "sources": ledger["coverage"],
        "all_available": bool(ledger["coverage"])
        and all(
            str(source.get("status") or "").lower() == "available" for source in ledger["coverage"]
        ),
    }
    return envelope(data, as_of, hit)


@router.get("/budgets")
def budgets(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days, 1, 400)
    ledger, as_of, hit = _ledger(days, refresh)
    return envelope(ledger["budgets"], as_of, hit)

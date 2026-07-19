"""Drift monitoring for the AI Cost Planner's saved estimates.

Two independent checks, both following the ``forecast_monitor`` shape (pure
``classify_*`` over rows fetched by thin impure readers, then
``digest.store_findings``):

- **Re-price staleness** (``classify_repricing_drift``): re-run each saved
  estimate through the deterministic engine at *current* prices and flag those
  whose production total has moved materially since it was saved. Needs no
  attribution — a saved estimate carries its own requirements. Key
  ``cost/estimate-repricing-drift``.
- **Estimate-vs-actuals** (``classify_actuals_drift``, Commit C): compare an
  *activated* estimate's projected monthly cost against the trailing-month
  real spend for the cost anchor an operator linked it to. Key
  ``cost/estimate-drift``.

Everything numeric is pure and unit-tested offline; only the ``fetch_*``
readers touch the warehouse.
"""

from __future__ import annotations

import json

from databricks.sdk import WorkspaceClient

from dbx_platform.estimator import (
    SCENARIOS,
    compute_matrix,
    load_patterns,
    load_rate_card,
    load_tiers,
    validate_requirements,
)
from dbx_platform.system_tables import run_query

REPRICING_THRESHOLD_PCT = 15.0
REPRICING_CHECK = "cost/estimate-repricing-drift"


# --- readers (impure) ---------------------------------------------------------


def fetch_saved_estimates(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    *,
    workspace_id: str,
    environment: str,
    limit: int = 500,
) -> list[dict]:
    """Saved estimates for this deployment scope, newest first. Pure SQL."""

    sql = (
        "SELECT estimate_id, title, requirements_json, snapshot_date, "
        "rate_card_version, rigor_pct, results_json "
        f"FROM {catalog}.{schema}.estimator_estimates "
        "WHERE workspace_id = :workspace_id AND environment = :environment "
        "ORDER BY created_at DESC LIMIT :limit"
    )
    return run_query(
        w,
        sql,
        warehouse_id,
        {"workspace_id": workspace_id, "environment": environment, "limit": int(limit)},
    )


# --- re-price staleness (pure) ------------------------------------------------


def _stored_prod_total(stored: dict, tier: str, scenario: str) -> float | None:
    try:
        return float(
            stored["tiers"][tier]["scenarios"][scenario]["totals_by_env"]["prod"]
        )
    except (KeyError, TypeError, ValueError):
        return None


def classify_repricing_drift(
    estimates: list[dict],
    price_book,
    *,
    threshold_pct: float = REPRICING_THRESHOLD_PCT,
    rate_card: dict | None = None,
    tiers: dict | None = None,
    patterns: dict | None = None,
) -> list[dict]:
    """Flag saved estimates whose production total moved at current prices. Pure.

    Compares the **production** tier (both scenarios) of each stored estimate
    against a recompute at the current price book, reporting the scenario with
    the largest relative move. Estimates priced at the current snapshot are
    skipped (nothing could have moved); a recompute with unpriced components is
    not trusted for that scenario.
    """
    rate_card = rate_card or load_rate_card()
    tiers = tiers or load_tiers()
    patterns = patterns or load_patterns()
    current_snapshot = price_book.snapshot_date
    findings: list[dict] = []
    for est in estimates:
        stored_snapshot = str(est.get("snapshot_date") or "")
        if stored_snapshot and stored_snapshot == current_snapshot:
            continue
        try:
            stored = json.loads(est.get("results_json") or "{}")
            req = validate_requirements(
                json.loads(est.get("requirements_json") or "{}"), patterns
            )
        except (ValueError, TypeError):
            continue
        rigor = int(est.get("rigor_pct") or stored.get("rigor_pct") or 10)
        current = compute_matrix(
            req, rigor_pct=rigor, price_book=price_book, tiers=tiers, patterns=patterns
        )
        worst: tuple[str, float, float, float] | None = None
        for scenario in SCENARIOS:
            cur_est = current["tiers"]["production"]["scenarios"][scenario]
            if cur_est["missing_prices"]:
                continue
            new_total = float(cur_est["totals_by_env"]["prod"])
            old_total = _stored_prod_total(stored, "production", scenario)
            if old_total is None or old_total <= 0:
                continue
            change_pct = (new_total - old_total) / old_total * 100
            if worst is None or abs(change_pct) > abs(worst[2]):
                worst = (scenario, new_total, change_pct, old_total)
        if worst is None:
            continue
        scenario, new_total, change_pct, old_total = worst
        if abs(change_pct) < threshold_pct:
            continue
        title = est.get("title") or est.get("estimate_id")
        findings.append(
            {
                "estimate_id": est.get("estimate_id"),
                "resource": est.get("estimate_id"),
                "title": title,
                "scenario": scenario,
                "reason": (
                    f"saved estimate '{title}' priced at {stored_snapshot or 'unknown'} "
                    f"was ${old_total:,.0f}/mo (production, {scenario}); at current prices "
                    f"it is ${new_total:,.0f}/mo ({change_pct:+.0f}%)"
                ),
                "action": "re-estimate",
                "cost_usd": round(abs(new_total - old_total), 2),
                "old_snapshot": stored_snapshot,
                "current_snapshot": current_snapshot,
            }
        )
    findings.sort(key=lambda f: f["cost_usd"], reverse=True)
    return findings


# --- storage ------------------------------------------------------------------


def store_findings(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    findings_by_check: dict[str, list[dict]],
    *,
    workspace_id: str,
    environment: str,
) -> int:
    """Merge lifecycle-aware, workspace-scoped estimate-drift findings."""

    from dbx_platform.digest import store_findings as store_canonical_findings

    return store_canonical_findings(
        w,
        warehouse_id,
        catalog,
        schema,
        findings_by_check,
        workspace_id=workspace_id,
        environment=environment,
    )

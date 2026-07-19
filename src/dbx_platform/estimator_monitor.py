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
ACTUALS_THRESHOLD_PCT = 25.0
REPRICING_CHECK = "cost/estimate-repricing-drift"
ACTUALS_CHECK = "cost/estimate-drift"

_TAG_ANCHORS = {
    "databricks_project_tag": "project",
    "databricks_team_tag": "team",
}


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


def fetch_active_deployments(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    *,
    workspace_id: str,
    environment: str,
) -> list[dict]:
    """Latest active deploy-link per estimate for this scope. Pure SQL.

    The link table is append-only: the newest row per (estimate_id) wins, and
    a link is live only when that newest row has ``active = true`` (a retire
    appends ``active=false``).
    """
    sql = (
        "WITH ranked AS ("
        "SELECT *, ROW_NUMBER() OVER ("
        "PARTITION BY estimate_id ORDER BY created_at DESC) AS rn "
        f"FROM {catalog}.{schema}.estimator_deployments "
        "WHERE workspace_id = :workspace_id AND environment = :environment) "
        "SELECT deployment_id, estimate_id, tier, scenario, anchor_kind, "
        "anchor_value, monthly_projected_usd, currency "
        "FROM ranked WHERE rn = 1 AND active = true"
    )
    return run_query(
        w, sql, warehouse_id, {"workspace_id": workspace_id, "environment": environment}
    )


def fetch_anchor_actuals(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    deployments: list[dict],
    *,
    days: int = 30,
) -> dict[tuple[str, str], float]:
    """Trailing-``days`` actual spend per distinct (anchor_kind, value). Impure.

    ``azure_resource_group`` sums the ingested Azure bill (AZURE_ACTUAL basis);
    the Databricks tag anchors use ``cost.attribution`` list cost
    (DATABRICKS_LIST basis). Anchors with no rows yet return no entry (the
    classifier skips too-new deployments rather than flagging a phantom gap).
    """
    from dbx_platform import cost

    actuals: dict[tuple[str, str], float] = {}
    wanted = {(str(d["anchor_kind"]), str(d["anchor_value"])) for d in deployments}
    for kind, value in wanted:
        if kind == "azure_resource_group":
            rows = run_query(
                w,
                "SELECT ROUND(SUM(cost), 2) AS actual FROM "
                f"{catalog}.{schema}.azure_costs "
                "WHERE resource_group = :rg "
                "AND usage_date >= DATE_SUB(CURRENT_DATE(), :days)",
                warehouse_id,
                {"rg": value, "days": int(days)},
            )
            total = rows[0].get("actual") if rows else None
        elif kind in _TAG_ANCHORS:
            rows = cost.attribution(w, warehouse_id, _TAG_ANCHORS[kind], days)
            tag_col = f"x_{_TAG_ANCHORS[kind]}"
            total = next(
                (r.get("list_cost") for r in rows if str(r.get(tag_col)) == value), None
            )
        else:
            continue
        if total is not None:
            actuals[(kind, value)] = float(total)
    return actuals


def classify_actuals_drift(
    deployments: list[dict],
    actuals: dict[tuple[str, str], float],
    *,
    threshold_pct: float = ACTUALS_THRESHOLD_PCT,
) -> list[dict]:
    """Compare each active link's projection to its anchor's real spend. Pure.

    A deployment whose anchor has no actuals yet (too new to have accrued a
    month) is skipped, never flagged. The finding is honest that the anchor
    may include non-AI resources, so this is a directional signal, not an audit.
    """
    findings: list[dict] = []
    for dep in deployments:
        anchor = (str(dep.get("anchor_kind")), str(dep.get("anchor_value")))
        actual = actuals.get(anchor)
        projected = float(dep.get("monthly_projected_usd") or 0.0)
        if actual is None or projected <= 0:
            continue
        change_pct = (actual - projected) / projected * 100
        if abs(change_pct) < threshold_pct:
            continue
        basis = (
            "Azure actual bill"
            if anchor[0] == "azure_resource_group"
            else "Databricks list cost"
        )
        findings.append(
            {
                "deployment_id": dep.get("deployment_id"),
                "estimate_id": dep.get("estimate_id"),
                "resource": anchor[1],
                "reason": (
                    f"projected ${projected:,.0f}/mo ({dep.get('tier')}, "
                    f"{dep.get('scenario')}); {basis} for {anchor[0]} '{anchor[1]}' "
                    f"is ${actual:,.0f}/mo over the last 30 days ({change_pct:+.0f}%). "
                    "The anchor may include non-AI resources — treat as a "
                    "directional signal."
                ),
                "action": "review-estimate-vs-actual",
                "cost_usd": round(abs(actual - projected), 2),
                "anchor_kind": anchor[0],
            }
        )
    findings.sort(key=lambda f: f["cost_usd"], reverse=True)
    return findings


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


def run_drift_check(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    *,
    workspace_id: str,
    environment: str,
    days: int = 30,
    reprice_threshold_pct: float = REPRICING_THRESHOLD_PCT,
    actuals_threshold_pct: float = ACTUALS_THRESHOLD_PCT,
) -> dict[str, list[dict]]:
    """Run both drift checks; return findings keyed by check. Impure reads.

    Needs the current price book (re-price) plus the deploy-links and their
    anchor actuals. Returns ``{}`` for a check whose inputs are unavailable so
    a partial run still stores what it can.
    """
    from dbx_platform.estimator import build_price_book, load_rate_card
    from dbx_platform.estimator_pricing import read_latest_snapshot

    findings: dict[str, list[dict]] = {REPRICING_CHECK: [], ACTUALS_CHECK: []}

    estimates = fetch_saved_estimates(
        w, warehouse_id, catalog, schema,
        workspace_id=workspace_id, environment=environment,
    )
    snapshot = read_latest_snapshot(
        w, warehouse_id, catalog, schema, environment=environment,
    )
    if estimates and snapshot:
        book = build_price_book(snapshot, load_rate_card())
        findings[REPRICING_CHECK] = classify_repricing_drift(
            estimates, book, threshold_pct=reprice_threshold_pct
        )

    deployments = fetch_active_deployments(
        w, warehouse_id, catalog, schema,
        workspace_id=workspace_id, environment=environment,
    )
    if deployments:
        actuals = fetch_anchor_actuals(
            w, warehouse_id, catalog, schema, deployments, days=days
        )
        findings[ACTUALS_CHECK] = classify_actuals_drift(
            deployments, actuals, threshold_pct=actuals_threshold_pct
        )
    return findings


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

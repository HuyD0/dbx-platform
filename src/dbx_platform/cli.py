"""dbx-platform CLI — single entry point for ad-hoc admin use and scheduled jobs.

The bundle's job tasks invoke this exact entry point (python_wheel_task with
``entry_point: dbx-platform``), so scheduled jobs exercise the same code path
you test locally.

Safety: legacy mutating commands remain useful as dry-run planners, but direct
``--apply`` execution has been removed. Resource changes must flow through an
immutable Mission Control action and the dedicated executor job.

Output: ``--output table`` (default) or ``--output json`` (one JSON document
per report block, NDJSON-friendly).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from importlib import resources

from dbx_platform import __version__, cost, governance, housekeeping, ml, security
from dbx_platform.client import get_client
from dbx_platform.config import Settings
from dbx_platform.system_tables import SystemTablesUnavailableError

# --- output helpers ---------------------------------------------------------


def _render_table(rows: list[dict]) -> str:
    if not rows:
        return "  (none)"
    cols: list[str] = []
    for r in rows:
        for k in r:
            if not k.startswith("_") and k not in cols:
                cols.append(k)
    cell = lambda v: (str(v)[:57] + "...") if len(str(v)) > 60 else str(v)  # noqa: E731
    widths = {c: max(len(c), *(len(cell(r.get(c, ""))) for r in rows)) for c in cols}
    lines = [
        "  " + " | ".join(c.ljust(widths[c]) for c in cols),
        "  " + "-+-".join("-" * widths[c] for c in cols),
    ]
    for r in rows:
        lines.append("  " + " | ".join(cell(r.get(c, "")).ljust(widths[c]) for c in cols))
    return "\n".join(lines)


def emit(args, title: str, rows: list[dict], notes: list[str] | None = None) -> None:
    notes = notes or []
    if args.output == "json":
        print(
            json.dumps(
                {"title": title, "count": len(rows), "rows": rows, "notes": notes}, default=str
            )
        )
        return
    print(f"\n== {title} ({len(rows)}) ==")
    print(_render_table(rows))
    for n in notes:
        print(f"  note: {n}")


def check_apply(args) -> bool:
    """Keep legacy report commands dry-run and reject every direct mutation."""
    if not getattr(args, "apply", False):
        return False
    print(
        "error: direct --apply execution was removed. Create and approve an "
        "immutable request in Mission Control; the dedicated executor will "
        "revalidate and apply it exactly once.",
        file=sys.stderr,
    )
    raise SystemExit(2)


def _reject_direct_state_change(capability: str) -> int:
    print(
        f"error: direct {capability} is disabled. Use an immutable Mission "
        "Control plan and the dedicated executor, or the documented "
        "deployment-only schema migration.",
        file=sys.stderr,
    )
    return 2


def _verify_governed_write(args, w, settings: Settings) -> bool:
    from dbx_platform.approved_job import ApprovalGateError, verify_governed_write_launch

    try:
        verify_governed_write_launch(
            w,
            _warehouse_id(args, settings),
            catalog=settings.dashboard_catalog,
            schema=settings.dashboard_schema,
            environment=args.environment,
            action_id=args.approved_action_id,
            plan_hash=args.approved_plan_hash,
            job_id=args.approved_job_id,
            run_id=args.approved_run_id,
            trigger_type=args.trigger_type,
        )
        return True
    except ApprovalGateError as exc:
        print(f"error: governed Job context rejected: {exc}", file=sys.stderr)
        return False


def _store_cost_findings(args, w, settings: Settings, check_key: str,
                         findings: list[dict]) -> int:
    """Persist a report command's findings inside a verified governed run.

    The check key is stored even when ``findings`` is empty so previously OPEN
    rows for the check auto-resolve once the condition clears.
    """
    from dbx_platform import digest

    return digest.store_findings(
        w,
        _warehouse_id(args, settings),
        settings.dashboard_catalog,
        settings.dashboard_schema,
        {check_key: findings},
        workspace_id=str(w.get_workspace_id()),
        environment=getattr(args, "environment", settings.environment),
    )


def _warehouse_id(args, settings: Settings) -> str:
    return args.warehouse_id or settings.warehouse_id


def _now_ms() -> int:
    return int(time.time() * 1000)


# --- cost --------------------------------------------------------------------


def cmd_cost_report(args) -> int:
    s = Settings.from_env()
    w = get_client(args.profile)
    days = args.days if args.days is not None else s.lookback_days
    rows = cost.usage_report(w, _warehouse_id(args, s), days)
    emit(args, f"DBU + list cost by SKU/workspace — last {days}d", rows)
    return 0


def cmd_cost_top_jobs(args) -> int:
    s = Settings.from_env()
    w = get_client(args.profile)
    days = args.days if args.days is not None else s.lookback_days
    rows = cost.top_jobs(w, _warehouse_id(args, s), days, args.limit)
    emit(args, f"Top {args.limit} most expensive jobs — last {days}d", rows)
    return 0


def cmd_cost_cluster_utilization(args) -> int:
    s = Settings.from_env()
    w = get_client(args.profile)
    store = getattr(args, "store_findings", False)
    if store and not _verify_governed_write(args, w, s):
        return 2
    days = args.days if args.days is not None else s.lookback_days
    cpu = args.cpu_threshold if args.cpu_threshold is not None else s.util_cpu_threshold_pct
    mem = args.mem_threshold if args.mem_threshold is not None else s.util_mem_threshold_pct
    rows = cost.cluster_utilization(w, _warehouse_id(args, s), days)
    findings = cost.classify_cluster_utilization(rows, cpu, mem)
    notes = ["Report only — right-sizing is applied by the cluster owner (see docs/runbook.md)."]
    if store:
        stored = _store_cost_findings(args, w, s, "cost/cluster-underutilized", findings)
        notes.append(f"{stored} finding rows stored; right-sized clusters auto-resolve")
    emit(
        args,
        f"Under-utilized clusters — last {days}d (ranked by cost)",
        findings,
        notes,
    )
    return 0


def cmd_cost_attribution(args) -> int:
    s = Settings.from_env()
    w = get_client(args.profile)
    days = args.days if args.days is not None else s.lookback_days
    rows = cost.attribution(w, _warehouse_id(args, s), args.dimension, days)
    emit(
        args,
        f"List cost by {args.dimension} — last {days}d",
        rows,
        ["'unallocated' rows carry none of the tags the cluster policies enforce."],
    )
    return 0


def cmd_cost_failed_run_waste(args) -> int:
    s = Settings.from_env()
    w = get_client(args.profile)
    days = args.days if args.days is not None else s.lookback_days
    rows = cost.failed_run_waste(w, _warehouse_id(args, s), days, args.limit)
    emit(args, f"List cost burned on failed job runs — last {days}d", rows)
    return 0


def cmd_cost_warehouse_utilization(args) -> int:
    s = Settings.from_env()
    w = get_client(args.profile)
    store = getattr(args, "store_findings", False)
    if store and not _verify_governed_write(args, w, s):
        return 2
    days = args.days if args.days is not None else s.lookback_days
    rows = cost.warehouse_utilization(w, _warehouse_id(args, s), days)
    findings = cost.classify_warehouse_utilization(
        rows, s.warehouse_min_queries, s.warehouse_queue_warn_seconds
    )
    notes = ["Report only — includes both directions: idle spend and sustained queueing."]
    if store:
        stored = _store_cost_findings(args, w, s, "cost/warehouse-mis-sized", findings)
        notes.append(f"{stored} finding rows stored; corrected warehouses auto-resolve")
    emit(
        args,
        f"Mis-sized SQL warehouses — last {days}d",
        findings,
        notes,
    )
    return 0


# --- LLM cost ----------------------------------------------------------------


def cmd_llm_cost_rollup(args) -> int:
    """Normalize Databricks, AI Gateway and Azure AI usage into UC tables."""

    from datetime import date, timedelta

    s = Settings.from_env()
    w = get_client(args.profile)
    if not _verify_governed_write(args, w, s):
        return 2
    from dbx_platform import llm_cost

    warehouse = _warehouse_id(args, s)
    days = args.days if args.days is not None else 3
    workspace_id = str(w.get_workspace_id())
    environment = getattr(args, "environment", s.environment)
    window_end = date.today()
    window_start = window_end - timedelta(days=days)
    notes: list[str] = []
    costs: list[dict] = []
    usage: list[dict] = []
    cost_scopes: list[dict[str, str]] = []
    usage_scopes: list[dict[str, str]] = []
    source_health: list[dict] = []

    try:
        databricks_rows = llm_cost.databricks_cost(w, warehouse, days, gateway_enriched=True)
        billing_status = "available"
        billing_note = "Unity AI Gateway billing attribution available"
    except Exception as enriched_error:  # noqa: BLE001 - feature detection
        try:
            databricks_rows = llm_cost.databricks_cost(w, warehouse, days, gateway_enriched=False)
            billing_status = "partial"
            billing_note = (
                "Cost available without Gateway model/tag attribution "
                f"({enriched_error.__class__.__name__})"
            )
            notes.append(
                "Unity AI Gateway billing attribution unavailable; used compatibility query"
            )
        except Exception as compatibility_error:  # noqa: BLE001
            databricks_rows = []
            billing_status = "unavailable"
            billing_note = compatibility_error.__class__.__name__
            notes.append(
                "Databricks hosted model billing unavailable "
                f"({compatibility_error.__class__.__name__})"
            )
    if billing_status != "unavailable":
        normalized_databricks = llm_cost.normalize_cost_rows(
            databricks_rows,
            "system.billing.usage",
            "DATABRICKS_LIST",
            environment=environment,
            workspace_id=workspace_id,
        )
        if any(row["currency"] == "UNKNOWN" for row in normalized_databricks):
            billing_status = "partial"
            billing_note += "; rows with missing currency are isolated as UNKNOWN"
        costs.extend(normalized_databricks)
        cost_scopes.append(
            {
                "workspace_id": workspace_id,
                "environment": environment,
                "source": "system.billing.usage",
                "cost_basis": "DATABRICKS_LIST",
            }
        )
    source_health.append(
        llm_cost.coverage_record(
            "Databricks hosted model billing",
            billing_status,
            source_key="databricks-hosted-billing",
            source_type="cost",
            freshness="throughout the day",
            retention_days=400,
            cost_basis="DATABRICKS_LIST",
            coverage_start=(window_start.isoformat() if billing_status != "unavailable" else None),
            coverage_end=(window_end.isoformat() if billing_status != "unavailable" else None),
            row_count=len(databricks_rows),
            available_metrics=["cost", "currency", "cost_basis"],
            notes=billing_note,
        )
    )

    try:
        external = llm_cost.external_model_spend(w, warehouse, days)
        normalized_external = llm_cost.normalize_cost_rows(
            external,
            "system.ai_gateway.external_model_spend",
            "PROVIDER_ESTIMATE",
            environment=environment,
            workspace_id=workspace_id,
        )
        costs.extend(normalized_external)
        cost_scopes.append(
            {
                "workspace_id": workspace_id,
                "environment": environment,
                "source": "system.ai_gateway.external_model_spend",
                "cost_basis": "PROVIDER_ESTIMATE",
            }
        )
        external_status = (
            "partial"
            if any(row["currency"] == "UNKNOWN" for row in normalized_external)
            else "available"
        )
        external_note = "Beta; based on published provider prices"
        if external_status == "partial":
            external_note += "; rows with missing currency are isolated as UNKNOWN"
    except Exception as error:  # noqa: BLE001 - optional Beta source
        external = []
        external_status = "unavailable"
        external_note = f"Optional Beta source: {error.__class__.__name__}"
        notes.append(f"external model estimates unavailable ({error.__class__.__name__})")
    source_health.append(
        llm_cost.coverage_record(
            "Unity AI Gateway external model spend",
            external_status,
            source_key="ai-gateway-external-model-spend",
            source_type="cost",
            freshness="hourly aggregate",
            retention_days=400,
            cost_basis="PROVIDER_ESTIMATE",
            coverage_start=(
                window_start.isoformat() if external_status != "unavailable" else None
            ),
            coverage_end=(
                window_end.isoformat() if external_status != "unavailable" else None
            ),
            row_count=len(external),
            available_metrics=["cost", "currency", "cost_basis"],
            notes=external_note,
        )
    )

    try:
        azure_result = llm_cost.azure_actual_cost(
            w,
            warehouse,
            s.dashboard_catalog,
            s.dashboard_schema,
            days,
            workspace_id=workspace_id,
            environment=environment,
        )
        azure = azure_result.rows
        normalized_azure = llm_cost.normalize_cost_rows(
            azure,
            "azure_costs",
            "AZURE_ACTUAL",
            environment=environment,
            workspace_id=workspace_id,
        )
        costs.extend(normalized_azure)
        cost_scopes.append(
            {
                "workspace_id": workspace_id,
                "environment": environment,
                "source": "azure_costs",
                "cost_basis": "AZURE_ACTUAL",
            }
        )
        azure_status = azure_result.status
        azure_note = (
            f"{azure_result.notes}; late adjustments may restate prior days"
        )
        if any(row["currency"] == "UNKNOWN" for row in normalized_azure):
            azure_status = "partial"
            azure_note += "; rows with missing currency are isolated as UNKNOWN"
    except Exception as error:  # noqa: BLE001 - Azure integration is optional
        azure = []
        azure_status = "unavailable"
        azure_note = f"Azure integration unavailable: {error.__class__.__name__}"
        notes.append(f"Azure AI actuals unavailable ({error.__class__.__name__})")
    source_health.append(
        llm_cost.coverage_record(
            "Azure Cost Management",
            azure_status,
            source_key="azure-cost-management",
            source_type="cost",
            freshness="daily; subject to late adjustments",
            retention_days=400,
            cost_basis="AZURE_ACTUAL",
            coverage_start=(
                window_start.isoformat() if azure_status != "unavailable" else None
            ),
            coverage_end=(
                window_end.isoformat() if azure_status != "unavailable" else None
            ),
            row_count=len(azure),
            available_metrics=["cost", "currency", "cost_basis"],
            notes=azure_note,
        )
    )

    try:
        request_rows = llm_cost.gateway_usage(w, warehouse, min(days, 90))
        usage = llm_cost.normalize_usage_rows(
            request_rows,
            "system.ai_gateway.usage",
            environment=environment,
            workspace_id=workspace_id,
        )
        usage_scopes.append(
            {
                "workspace_id": workspace_id,
                "environment": environment,
                "source": "system.ai_gateway.usage",
            }
        )
        # The Gateway and endpoint-usage sources are mutually exclusive views
        # of the same requests. Reconcile the inactive source to empty over the
        # same window so a feature-availability switch cannot double count.
        usage_scopes.append(
            {
                "workspace_id": workspace_id,
                "environment": environment,
                "source": "system.serving.endpoint_usage",
            }
        )
        usage_status = "available"
        usage_note = "Beta; includes latency, routing, cached and reasoning tokens"
        usage_metrics = [
            "requests",
            "successful_requests",
            "input_tokens",
            "output_tokens",
            "cached_tokens",
            "reasoning_tokens",
            "errors",
            "retries",
            "p95_latency_ms",
        ]
    except Exception as gateway_error:  # noqa: BLE001 - feature detection
        try:
            request_rows = llm_cost.endpoint_usage(w, warehouse, min(days, 90))
            usage = llm_cost.normalize_usage_rows(
                request_rows,
                "system.serving.endpoint_usage",
                environment=environment,
                workspace_id=workspace_id,
            )
            usage_scopes.append(
                {
                    "workspace_id": workspace_id,
                    "environment": environment,
                    "source": "system.serving.endpoint_usage",
                }
            )
            usage_scopes.append(
                {
                    "workspace_id": workspace_id,
                    "environment": environment,
                    "source": "system.ai_gateway.usage",
                }
            )
            usage_status = "partial"
            usage_note = (
                "Legacy serving usage has request/input/output tokens only "
                f"({gateway_error.__class__.__name__})"
            )
            usage_metrics = ["requests", "input_tokens", "output_tokens"]
            notes.append("Unity AI Gateway request usage unavailable; used serving usage")
        except Exception as compatibility_error:  # noqa: BLE001
            request_rows = []
            usage_status = "unavailable"
            usage_note = compatibility_error.__class__.__name__
            usage_metrics = []
            notes.append(
                f"Model request usage unavailable ({compatibility_error.__class__.__name__})"
            )
    source_health.append(
        llm_cost.coverage_record(
            "Model request usage",
            usage_status,
            source_key="model-request-usage",
            source_type="usage",
            freshness="throughout the day",
            retention_days=90,
            coverage_start=(window_start.isoformat() if usage_status != "unavailable" else None),
            coverage_end=(window_end.isoformat() if usage_status != "unavailable" else None),
            row_count=len(request_rows),
            available_metrics=usage_metrics,
            notes=usage_note,
        )
    )

    stored = llm_cost.store_ledger(
        w,
        warehouse,
        s.dashboard_catalog,
        s.dashboard_schema,
        costs,
        usage,
        window_start=window_start.isoformat(),
        window_end=window_end.isoformat(),
        cost_scopes=cost_scopes,
        usage_scopes=usage_scopes,
    )
    stored["source_health_rows"] = llm_cost.store_source_health(
        w,
        warehouse,
        s.dashboard_catalog,
        s.dashboard_schema,
        source_health,
        workspace_id=workspace_id,
        environment=environment,
    )
    # Budget breaches become canonical findings so Mission Control ranks and
    # digests them, instead of the state existing only on console page load.
    # The check key is always stored so cleared breaches auto-resolve.
    try:
        from dbx_platform import digest

        month_days = date.today().day
        evaluated = llm_cost.evaluate_budgets(
            llm_cost.budget_rows(
                w, warehouse, s.dashboard_catalog, s.dashboard_schema,
                workspace_id, environment,
            ),
            llm_cost.read_llm_cost_daily(
                w, warehouse, s.dashboard_catalog, s.dashboard_schema,
                workspace_id, environment, month_days,
            ),
        )
        stored["budget_breach_findings"] = digest.store_findings(
            w,
            warehouse,
            s.dashboard_catalog,
            s.dashboard_schema,
            {"cost/llm-budget-breach": llm_cost.classify_budget_findings(evaluated)},
            workspace_id=workspace_id,
            environment=environment,
        )
    except Exception as error:  # noqa: BLE001 - budgets are optional
        notes.append(f"budget-breach findings skipped ({error.__class__.__name__})")
    emit(
        args,
        f"LLM cost rollup — last {days}d",
        [
            {
                **stored,
                "cost_table": (f"{s.dashboard_catalog}.{s.dashboard_schema}.llm_cost_daily"),
                "usage_table": (f"{s.dashboard_catalog}.{s.dashboard_schema}.llm_usage_hourly"),
            }
        ],
        notes,
    )
    return 0


# --- azure-cost ---------------------------------------------------------------


def cmd_azure_cost_pull(args) -> int:
    from datetime import date

    s = Settings.from_env()
    w = get_client(args.profile)
    if not _verify_governed_write(args, w, s):
        return 2
    from dbx_platform import azure_cost, secrets

    sub = args.subscription_id or s.azure_subscription_id
    days = args.days if args.days is not None else 3
    end = date.today()
    start, end = azure_cost.inclusive_date_window(end, days)
    cred = secrets.get_credential(args.service_credential or None)
    pages = azure_cost.fetch_cost_query(cred, sub, start.isoformat(), end.isoformat())
    rows = azure_cost.parse_query_result(pages)
    workspace_id = str(w.get_workspace_id())
    environment = getattr(args, "environment", s.environment)
    n = azure_cost.store_costs(
        w,
        _warehouse_id(args, s),
        s.dashboard_catalog,
        s.dashboard_schema,
        rows,
        workspace_id=workspace_id,
        environment=environment,
        window_start=start.isoformat(),
        window_end=end.isoformat(),
    )
    try:
        detail_pages = azure_cost.fetch_cost_query(
            cred,
            sub,
            start.isoformat(),
            end.isoformat(),
            body=azure_cost.build_detail_query_body(start.isoformat(), end.isoformat()),
        )
        detail_rows = azure_cost.parse_detail_query_result(detail_pages)
    except Exception as error:  # noqa: BLE001 - coarse actuals remain authoritative
        detail_note = (
            "resource/meter allocation unavailable; coarse actuals succeeded "
            f"({error.__class__.__name__})"
        )
    else:
        detail_count = azure_cost.store_detail_costs(
            w,
            _warehouse_id(args, s),
            s.dashboard_catalog,
            s.dashboard_schema,
            detail_rows,
            workspace_id=workspace_id,
            environment=environment,
            window_start=start.isoformat(),
            window_end=end.isoformat(),
        )
        detail_note = f"{detail_count} resource/meter rows merged for AI allocation"
    by_bucket: dict[str, float] = {}
    for r in rows:
        by_bucket[r["service_bucket"]] = by_bucket.get(r["service_bucket"], 0.0) + r["cost"]
    summary = [
        {"service_bucket": k, "cost": round(v, 2)}
        for k, v in sorted(by_bucket.items(), key=lambda kv: -kv[1])
    ]
    emit(
        args,
        f"Azure bill pull {start}..{end} — {n} rows merged into "
        f"{s.dashboard_catalog}.{s.dashboard_schema}.azure_costs",
        summary,
        [detail_note],
    )
    return 0


def cmd_azure_cost_report(args) -> int:
    from dbx_platform import azure_cost

    s = Settings.from_env()
    w = get_client(args.profile)
    days = args.days if args.days is not None else s.lookback_days
    rows = azure_cost.report(
        w,
        _warehouse_id(args, s),
        s.dashboard_catalog,
        s.dashboard_schema,
        args.by,
        days,
    )
    emit(args, f"Azure spend by {args.by} — last {days}d", rows)
    return 0


def cmd_azure_cost_spikes(args) -> int:
    from dbx_platform import azure_cost

    s = Settings.from_env()
    w = get_client(args.profile)
    store = getattr(args, "store_findings", False)
    if store and not _verify_governed_write(args, w, s):
        return 2
    days = args.days if args.days is not None else 14
    rows = azure_cost.fetch_daily_buckets(
        w, _warehouse_id(args, s), s.dashboard_catalog, s.dashboard_schema, days
    )
    findings = azure_cost.classify_azure_spend(rows, s.azure_spike_pct, s.azure_spike_min_cost)
    notes = ["Report only — investigate the bucket's resources before acting."]
    if store:
        stored = _store_cost_findings(args, w, s, "cost/azure-spend-spike", findings)
        notes.append(f"{stored} finding rows stored; cleared spikes auto-resolve")
    emit(
        args,
        "Azure spend spikes by service bucket "
        f"(day vs trailing 7d, threshold {s.azure_spike_pct}%)",
        findings,
        notes,
    )
    return 0


def cmd_azure_cost_detail(args) -> int:
    from dbx_platform import azure_cost

    s = Settings.from_env()
    w = get_client(args.profile)
    days = args.days if args.days is not None else s.lookback_days
    rows = azure_cost.report_detail(
        w,
        _warehouse_id(args, s),
        s.dashboard_catalog,
        s.dashboard_schema,
        args.by,
        days,
        args.bucket or None,
    )
    scope = f" ({args.bucket})" if args.bucket else ""
    emit(args, f"Azure detail spend by {args.by}{scope} — last {days}d", rows)
    return 0


# --- estimator ----------------------------------------------------------------


def cmd_estimator_prices_pull(args) -> int:
    from datetime import date

    s = Settings.from_env()
    w = get_client(args.profile)
    if not _verify_governed_write(args, w, s):
        return 2
    from dbx_platform import digest, estimator, estimator_pricing

    rate_card = estimator.load_rate_card()
    snapshot_date = args.snapshot_date or date.today().isoformat()
    region = args.region or "eastus"
    currency = args.currency or "USD"
    items_by_group: dict[str, list[dict]] = {}
    for group, odata_filter in estimator_pricing.build_price_filters(
        rate_card, region, currency
    ):
        items_by_group[group] = estimator_pricing.fetch_retail_prices(
            odata_filter, currency=currency
        )
    rows = estimator_pricing.parse_retail_prices(items_by_group, rate_card, snapshot_date)
    dbx_rows = estimator_pricing.parse_databricks_prices(
        estimator_pricing.fetch_databricks_prices(w, _warehouse_id(args, s)),
        rate_card,
        snapshot_date,
    )
    rows.extend(dbx_rows)
    environment = getattr(args, "environment", s.environment)
    n = estimator_pricing.store_price_snapshot(
        w,
        _warehouse_id(args, s),
        s.dashboard_catalog,
        s.dashboard_schema,
        rows,
        snapshot_date=snapshot_date,
        environment=environment,
    )
    findings, notes = estimator_pricing.classify_price_coverage(
        rate_card, rows, snapshot_date
    )
    stored = digest.store_findings(
        w,
        _warehouse_id(args, s),
        s.dashboard_catalog,
        s.dashboard_schema,
        {"cost/estimator-pricing-coverage": findings},
        workspace_id=str(w.get_workspace_id()),
        environment=environment,
    )
    notes.append(f"{stored} coverage finding rows stored; matched keys auto-resolve")
    emit(
        args,
        f"Estimator price snapshot {snapshot_date} ({region}, {currency}) — {n} rows "
        f"merged into {s.dashboard_catalog}.{s.dashboard_schema}.estimator_price_snapshots",
        findings,
        notes,
    )
    return 0


def cmd_estimator_prices_status(args) -> int:
    from dbx_platform import estimator_pricing

    s = Settings.from_env()
    w = get_client(args.profile)
    rows = estimator_pricing.read_snapshot_status(
        w,
        _warehouse_id(args, s),
        s.dashboard_catalog,
        s.dashboard_schema,
        environment=getattr(args, "environment", s.environment),
    )
    emit(args, "Estimator price snapshot status", rows)
    return 0


def cmd_estimator_prompts_sync(args) -> int:
    """Deployment-run prompt lineage: wheel texts -> UC prompt registry."""

    from dbx_platform import estimator_prompts

    s = Settings.from_env()
    try:
        results = estimator_prompts.register_prompts(
            s.dashboard_catalog, s.dashboard_schema
        )
    except ImportError:
        print(
            "error: mlflow>=3 is required (the estimator_prompt_sync job "
            "environment installs it).",
            file=sys.stderr,
        )
        return 2
    emit(args, "Estimator prompt registry sync", results)
    return 0


def cmd_estimator_eval_extraction(args) -> int:
    """Run the golden extraction dataset against a real endpoint; log to MLflow.

    Code scorers only (pattern accuracy, field tolerance, validation pass
    rate) — the same near-free checks the estimator recommends for its own
    users' prototype tier.
    """
    from dbx_platform import estimator, estimator_extract, estimator_prompts

    s = Settings.from_env()
    w = get_client(args.profile)
    endpoint = args.endpoint or s.digest_model
    dataset = estimator_extract.load_eval_dataset()
    model = estimator_extract.EndpointToolCaller(w, endpoint)
    scores = []
    rows = []
    for case in dataset:
        try:
            actual, _warnings = estimator_extract.extract_requirements(
                model, case["text"]
            )
        except Exception as error:  # noqa: BLE001 - a failed case scores zero
            actual = None
            rows.append({"case_id": case["case_id"], "error": error.__class__.__name__})
        score = estimator_extract.score_extraction(case["expected"], actual)
        scores.append(score)
        rows.append({"case_id": case["case_id"], **score})
    metrics = estimator_extract.aggregate_scores(scores)
    notes = [f"endpoint {endpoint}, {metrics['cases']} golden cases"]
    try:
        import mlflow

        mlflow.set_tracking_uri("databricks")
        mlflow.set_experiment(args.experiment)
        with mlflow.start_run(run_name="extraction-eval"):
            mlflow.log_params(
                {
                    "endpoint": endpoint,
                    "engine_version": estimator.ENGINE_VERSION,
                    **{
                        f"prompt_{spec['prompt']}": spec["content_hash"]
                        for spec in estimator_prompts.prompt_specs(
                            s.dashboard_catalog, s.dashboard_schema
                        )
                    },
                }
            )
            mlflow.log_metrics(metrics)
            mlflow.log_dict({"cases": dataset}, "extraction_eval_dataset.json")
        notes.append(f"logged to MLflow experiment {args.experiment}")
    except ImportError:
        notes.append("mlflow not installed; metrics reported only to stdout")
    emit(args, "Extraction eval (code scorers)", [metrics], notes)
    return 0 if metrics["pattern_accuracy"] >= args.min_pattern_accuracy else 1


def cmd_estimator_patterns(args) -> int:
    from dbx_platform import estimator

    patterns = estimator.load_patterns()["patterns"]
    rows = [
        {"pattern": key, "label": p["label"], "description": p["description"]}
        for key, p in sorted(patterns.items())
    ]
    emit(args, "AI solution patterns", rows)
    return 0


def cmd_estimator_estimate(args) -> int:
    """Compute one estimate from the latest stored price snapshot.

    Reads requirements from a JSON file so the same input document can be
    replayed byte-for-byte later — the CLI twin of the app's estimate API.
    """
    from dbx_platform import estimator, estimator_pricing

    s = Settings.from_env()
    with open(args.requirements_file, encoding="utf-8") as fh:
        raw = json.load(fh)
    try:
        req = estimator.validate_requirements(raw)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    w = get_client(args.profile)
    snapshot_rows = estimator_pricing.read_latest_snapshot(
        w,
        _warehouse_id(args, s),
        s.dashboard_catalog,
        s.dashboard_schema,
        environment=getattr(args, "environment", s.environment),
        currency=req.currency,
    )
    if not snapshot_rows:
        print(
            "error: no price snapshot found. Run the estimator-prices-pull job "
            "(or `estimator prices-pull` in a governed context) first.",
            file=sys.stderr,
        )
        return 2
    book = estimator.build_price_book(snapshot_rows, estimator.load_rate_card())
    matrix = estimator.compute_matrix(req, rigor_pct=args.rigor, price_book=book)
    if args.output == "json":
        print(json.dumps(matrix, default=str))
        return 0
    rows = []
    for tier, tier_data in matrix["tiers"].items():
        for scenario, est in tier_data["scenarios"].items():
            rows.append(
                {
                    "tier": tier,
                    "scenario": scenario,
                    **{f"{env}_monthly": est["totals_by_env"][env] for env in estimator.ENVS},
                    "eval_tax_prod": est["eval_tax_by_env"]["prod"],
                    "missing_prices": len(est["missing_prices"]),
                }
            )
    emit(
        args,
        f"TCO estimate — {req.pattern}, {req.monthly_requests:,} req/mo, "
        f"rigor {matrix['rigor_pct']}%, prices {matrix['snapshot_date']}",
        rows,
        [f"engine v{matrix['engine_version']}, rate card {matrix['rate_card_version']}"],
    )
    return 0


# --- forecast -----------------------------------------------------------------


def cmd_forecast_build_features(args) -> int:
    s = Settings.from_env()
    w = get_client(args.profile)
    if not _verify_governed_write(args, w, s):
        return 2
    from dbx_platform import azure_cost, forecast_features

    days = args.days if args.days is not None else 365
    rows = azure_cost.fetch_daily_buckets(
        w, _warehouse_id(args, s), s.dashboard_catalog, s.dashboard_schema, days
    )
    feats = forecast_features.build_features(rows)
    n = forecast_features.store_features(
        w, _warehouse_id(args, s), s.dashboard_catalog, s.dashboard_schema, feats
    )
    series = sorted({f["series"] for f in feats})
    emit(
        args,
        f"Cost features built (set v{forecast_features.FEATURE_SET_VERSION})",
        [
            {
                "series": ", ".join(series),
                "rows": n,
                "table": f"{s.dashboard_catalog}.{s.dashboard_schema}.cost_features",
            }
        ],
    )
    return 0


def cmd_forecast_train(args) -> int:
    from dbx_platform.approved_job import ApprovalGateError, verify_approved_job_launch

    s = Settings.from_env()
    w = get_client(args.profile)
    try:
        verify_approved_job_launch(
            w,
            _warehouse_id(args, s),
            catalog=s.dashboard_catalog,
            schema=s.dashboard_schema,
            environment=args.environment,
            action_id=args.approved_action_id,
            plan_hash=args.approved_plan_hash,
            job_id=args.approved_job_id,
            run_id=args.approved_run_id,
        )
    except ApprovalGateError as exc:
        print(f"error: forecast training approval rejected: {exc}", file=sys.stderr)
        return 2
    from dbx_platform import forecast_train

    rows = forecast_train.run_training(
        w,
        _warehouse_id(args, s),
        s.dashboard_catalog,
        s.dashboard_schema,
        args.model_name or s.forecast_model_name,
        args.experiment or s.forecast_experiment,
        n_folds=args.folds,
        horizon=args.horizon,
        min_improvement=args.min_improvement,
        allow_promote=not args.no_promote,
    )
    notes = ["Batch inference resolves the model by @champion alias only."]
    if args.no_promote:
        notes.append("Preview mode — the gate's decision was reported, @champion was not moved.")
    emit(args, "Forecast training — backtest + champion/challenger gate", rows, notes)
    return 0


def cmd_forecast_predict(args) -> int:
    s = Settings.from_env()
    w = get_client(args.profile)
    if not _verify_governed_write(args, w, s):
        return 2
    from dbx_platform import forecast_infer

    horizon = args.horizon if args.horizon is not None else s.forecast_horizon_days
    rows = forecast_infer.run_inference(
        w,
        _warehouse_id(args, s),
        s.dashboard_catalog,
        s.dashboard_schema,
        args.model_name or s.forecast_model_name,
        horizon,
    )
    emit(args, f"Azure cost forecast — next {horizon}d (P10/P50/P90)", rows)
    return 0


def cmd_forecast_monitor(args) -> int:
    s = Settings.from_env()
    w = get_client(args.profile)
    if not _verify_governed_write(args, w, s):
        return 2
    from dbx_platform import forecast_monitor

    warehouse = _warehouse_id(args, s)
    drift, errors, findings = forecast_monitor.run_monitoring(
        w, warehouse, s.dashboard_catalog, s.dashboard_schema
    )
    emit(args, "Feature drift (PSI vs reference window)", drift)
    emit(args, "Matured forecast accuracy by series", errors)
    emit(args, "Forecast monitor verdict", findings)
    if not args.no_store:
        try:
            forecast_monitor.store_findings(
                w, warehouse, s.dashboard_catalog, s.dashboard_schema, findings
            )
        except (SystemTablesUnavailableError, RuntimeError, ValueError) as e:
            print(
                f"  note: findings not stored ({e}) — run the deployment "
                "schema_migrations job first."
            )
    if any(f["action"] == "retrain-recommended" for f in findings):
        print("retrain recommended — failing so the job notification fires.", file=sys.stderr)
        return 1
    return 0


def cmd_forecast_status(args) -> int:
    s = Settings.from_env()
    try:
        import mlflow
        from mlflow.tracking import MlflowClient
    except ImportError:
        print(
            "error: forecasting libraries not installed. Run: pip install 'dbx-platform[forecast]'",
            file=sys.stderr,
        )
        return 2
    mlflow.set_registry_uri("databricks-uc")
    uc_name = (
        f"{s.dashboard_catalog}.{s.dashboard_schema}.{args.model_name or s.forecast_model_name}"
    )
    client = MlflowClient()
    rows = []
    for alias in ("champion", "challenger"):
        try:
            v = client.get_model_version_by_alias(uc_name, alias)
            rows.append(
                {
                    "alias": alias,
                    "version": v.version,
                    "backtest_wape": v.tags.get("backtest_wape", ""),
                    "run_id": v.run_id,
                }
            )
        except Exception as e:  # noqa: BLE001 — absent alias is a normal state
            rows.append(
                {
                    "alias": alias,
                    "version": "",
                    "backtest_wape": "",
                    "run_id": f"(none: {e.__class__.__name__})",
                }
            )
    emit(args, f"Forecaster registry status — {uc_name}", rows)
    return 0


# --- housekeeping -------------------------------------------------------------


def cmd_stale_clusters(args) -> int:
    s = Settings.from_env()
    apply_now = check_apply(args)
    w = get_client(args.profile)
    stale_days = args.stale_days if args.stale_days is not None else s.stale_cluster_days
    max_up = args.max_uptime_hours if args.max_uptime_hours is not None else s.max_uptime_hours
    findings = housekeeping.classify_clusters(
        housekeeping.fetch_clusters(w), _now_ms(), stale_days, max_up
    )
    notes = []
    if findings and not apply_now:
        notes.append(
            "Proposal only — create an exact stale-clusters action in Mission Control. "
            "Resource deletion is unsupported."
        )
    emit(args, "Stale / long-running clusters", findings, notes)
    if apply_now:
        for line in housekeeping.apply_cluster_findings(w, findings):
            print(f"  applied: {line}")
    return 0


def cmd_orphaned_jobs(args) -> int:
    apply_now = check_apply(args)
    w = get_client(args.profile)
    jobs = housekeeping.fetch_jobs(w)
    principals = housekeeping.fetch_active_principals(w)
    orphans = housekeeping.find_orphaned_jobs(jobs, principals)
    notes = []
    if orphans and not apply_now:
        notes.append("Proposal only — create an exact orphaned-jobs action in Mission Control.")
    emit(args, "Jobs with missing/inactive owners", orphans, notes)
    if apply_now:
        for o in orphans:
            if o.get("has_schedule") and housekeeping.pause_job(w, o["job_id"]):
                print(f"  applied: paused job {o['job_id']} ({o['name']})")
    return 0


def cmd_jobs_on_all_purpose(args) -> int:
    s = Settings.from_env()
    w = get_client(args.profile)
    findings = housekeeping.find_jobs_on_all_purpose(
        housekeeping.fetch_jobs_with_clusters(w), s.allpurpose_fixed_workers_max
    )
    emit(
        args,
        "Jobs on all-purpose compute / oversized fixed clusters",
        findings,
        [
            "Report only — moving a task to a job cluster is a job-spec change "
            "owned by the job's team."
        ],
    )
    return 0


# --- security ------------------------------------------------------------------


def cmd_token_audit(args) -> int:
    s = Settings.from_env()
    apply_now = check_apply(args)
    w = get_client(args.profile)
    max_age = args.max_age_days if args.max_age_days is not None else s.token_max_age_days
    findings = security.classify_tokens(
        security.fetch_tokens(w), _now_ms(), max_age, s.token_expiry_warn_days
    )
    notes = []
    if any(f["over_age"] for f in findings) and not apply_now:
        notes.append(
            "Proposal only — PAT revocation requires an exact Mission Control "
            "action and has no rollback."
        )
    emit(args, f"PAT audit (max age {max_age}d)", findings, notes)
    if apply_now:
        for line in security.revoke_tokens(w, findings):
            print(f"  applied: {line}")
    return 0


def cmd_inactive_users(args) -> int:
    s = Settings.from_env()
    w = get_client(args.profile)
    days = args.days if args.days is not None else s.inactive_user_days
    users = security.fetch_workspace_users(w)
    activity = security.fetch_user_activity(w, _warehouse_id(args, s), days)
    findings = security.find_inactive_users(users, activity, days)
    emit(
        args,
        f"Users with no audited activity in {days}d",
        findings,
        ["Report only — deactivation stays a human/IdP decision (see docs/runbook.md)."],
    )
    return 0


# --- governance ------------------------------------------------------------------


def _load_policies(policies_dir: str) -> list[dict]:
    if os.path.isdir(policies_dir):
        return governance.load_local_policies(policies_dir)
    # Inside a deployed wheel task there is no repo checkout; policies ship in the wheel.
    packaged = resources.files("dbx_platform") / "policies"
    return governance.load_local_policies(str(packaged))


def cmd_policy_sync(args) -> int:
    apply_now = check_apply(args)
    w = get_client(args.profile)
    plan = governance.diff_policies(
        _load_policies(args.policies_dir), governance.fetch_remote_policies(w)
    )
    rows = (
        [{"action": "create", "name": p["name"]} for p in plan["create"]]
        + [{"action": "update", "name": p["name"]} for p in plan["update"]]
        + [{"action": "unchanged", "name": p["name"]} for p in plan["unchanged"]]
        + [{"action": "unmanaged (left alone)", "name": p["name"]} for p in plan["unmanaged"]]
    )
    notes = []
    if (plan["create"] or plan["update"]) and not apply_now:
        notes.append("Proposal only — policy changes require an exact Mission Control action.")
    emit(args, "Cluster policy drift (git = source of truth)", rows, notes)
    if apply_now:
        for line in governance.apply_policy_plan(w, plan):
            print(f"  applied: {line}")
    return 0


def cmd_tag_compliance(args) -> int:
    s = Settings.from_env()
    w = get_client(args.profile)
    required = (
        [t.strip() for t in args.required_tags.split(",") if t.strip()]
        if args.required_tags
        else s.required_tag_list()
    )
    days = args.days if args.days is not None else s.lookback_days
    findings = governance.find_missing_tags(governance.fetch_taggable_resources(w), required)
    emit(args, f"Resources missing required tags {required}", findings)
    try:
        spend = governance.untagged_spend(w, _warehouse_id(args, s), days)
        emit(args, f"Untagged spend share — last {days}d", spend)
    except (SystemTablesUnavailableError, ValueError) as e:
        emit(args, "Untagged spend share", [], [f"skipped: {e}"])
    return 0


def cmd_tag_recommendations(args) -> int:
    s = Settings.from_env()
    w = get_client(args.profile)
    required = (
        [t.strip() for t in args.required_tags.split(",") if t.strip()]
        if args.required_tags
        else s.required_tag_list()
    )
    recs = governance.recommend_tags(
        governance.fetch_taggable_resources(w),
        required,
        min_ratio=s.tag_suggestion_min_ratio_pct / 100,
        owner_keys=tuple(s.tag_owner_key_list()),
    )
    emit(
        args,
        f"Tag recommendations for {required}",
        recs,
        ["Suggestions only — apply tag changes manually."],
    )
    return 0


# --- ml ----------------------------------------------------------------------------


def cmd_ml_endpoint_audit(args) -> int:
    s = Settings.from_env()
    w = get_client(args.profile)
    endpoints = ml.fetch_serving_endpoints(w)
    findings = ml.classify_serving_endpoints(endpoints, _now_ms(), s.serving_failed_grace_hours)
    emit(
        args,
        "Model serving endpoint audit",
        findings,
        [
            "Report only — endpoint config changes trigger a redeployment; apply "
            "manually (see docs/runbook.md)."
        ],
    )
    days = args.stale_days if args.stale_days is not None else s.serving_stale_days
    try:
        usage = ml.endpoint_token_usage(w, _warehouse_id(args, s), days)
        stale = ml.find_stale_endpoints(endpoints, usage, _now_ms(), days)
        emit(args, f"Endpoints with no requests in {days}d", stale)
    except (SystemTablesUnavailableError, ValueError) as e:
        emit(args, "Endpoints with no requests", [], [f"skipped: {e}"])
    return 0


def cmd_ml_model_hygiene(args) -> int:
    s = Settings.from_env()
    w = get_client(args.profile)
    stale = args.stale_days if args.stale_days is not None else s.model_stale_days
    unaliased = args.unaliased_days if args.unaliased_days is not None else s.model_unaliased_days
    models, truncated = ml.fetch_registered_models(w, args.catalog, args.schema, s.ml_max_models)
    served = ml.served_entity_names(ml.fetch_serving_endpoints(w))
    findings = ml.classify_models(models, served, _now_ms(), stale, unaliased)
    notes = ["Report only — archiving/deleting models stays a human decision."]
    if truncated:
        notes.append(
            f"listing truncated at {s.ml_max_models} models — "
            "narrow with --catalog/--schema for full coverage."
        )
    emit(args, f"Model registry hygiene ({len(models)} models checked)", findings, notes)
    return 0


def cmd_ml_serving_cost(args) -> int:
    s = Settings.from_env()
    w = get_client(args.profile)
    days = args.days if args.days is not None else s.lookback_days
    rows = ml.serving_cost(w, _warehouse_id(args, s), days)
    emit(args, f"AI/ML spend by product/SKU/endpoint — last {days}d", rows)
    try:
        tokens = ml.endpoint_token_usage(w, _warehouse_id(args, s), days)
        emit(args, f"Token usage by endpoint/requester — last {days}d", tokens)
    except SystemTablesUnavailableError as e:
        emit(args, "Token usage by endpoint/requester", [], [f"skipped: {e}"])
    return 0


def cmd_ml_gpu_audit(args) -> int:
    s = Settings.from_env()
    w = get_client(args.profile)
    max_up = args.max_uptime_hours if args.max_uptime_hours is not None else s.gpu_max_uptime_hours
    findings = ml.classify_gpu_clusters(
        ml.fetch_clusters_with_node_types(w), ml.fetch_gpu_node_types(w), _now_ms(), max_up
    )
    emit(
        args,
        f"Interactive GPU clusters (uptime threshold {max_up}h)",
        findings,
        ["Report only — create a reviewed proposal or contact the owner."],
    )
    days = args.days if args.days is not None else s.lookback_days
    try:
        spend = ml.gpu_spend(w, _warehouse_id(args, s), days)
        emit(args, f"GPU spend share — last {days}d", spend)
    except (SystemTablesUnavailableError, ValueError) as e:
        emit(args, "GPU spend share", [], [f"skipped: {e}"])
    return 0


def cmd_ml_vector_search_audit(args) -> int:
    s = Settings.from_env()
    w = get_client(args.profile)
    grace_hours = (
        args.grace_minutes / 60
        if args.grace_minutes is not None
        else s.vector_search_grace_hours
    )
    findings = ml.find_vector_search_findings(
        ml.fetch_vector_search(w), _now_ms(), grace_hours
    )
    emit(
        args,
        "Vector search endpoint audit",
        findings,
        ["Report only — endpoint deletion is irreversible and stays a human decision."],
    )
    return 0


# --- ai-catalog --------------------------------------------------------------------


def cmd_ai_catalog_sync(args) -> int:
    """Snapshot AI models + the identities that can access them into UC tables."""

    s = Settings.from_env()
    w = get_client(args.profile)
    if not args.no_store and not _verify_governed_write(args, w, s):
        return 2
    from dbx_platform import ai_catalog, digest, llm_cost, secrets

    warehouse = _warehouse_id(args, s)
    workspace_id = str(w.get_workspace_id())
    environment = getattr(args, "environment", s.environment)
    notes: list[str] = []
    catalog_rows: list[dict] = []
    access_rows: list[dict] = []
    refreshed: list[str] = []
    source_health: list[dict] = []

    try:
        models, truncated = ml.fetch_registered_models(w, None, None, s.ml_max_models)
        grants, grant_errors = ai_catalog.fetch_model_grants(
            w, [m["full_name"] for m in models]
        )
        catalog_rows.extend(ai_catalog.normalize_registered_models(models))
        access_rows.extend(ai_catalog.normalize_uc_grants(grants))
        refreshed.append("databricks_uc")
        uc_status = "partial" if (truncated or grant_errors) else "available"
        uc_note = "UC registered models and their grants"
        if truncated:
            uc_note += f"; listing capped at {s.ml_max_models} models"
        if grant_errors:
            uc_note += f"; {grant_errors} models had unreadable grants"
        uc_rows = len(models)
    except Exception as error:  # noqa: BLE001 - one surface never kills the sync
        uc_status, uc_rows = "unavailable", 0
        uc_note = f"UC model listing unavailable: {error.__class__.__name__}"
        notes.append(f"databricks_uc unavailable ({error.__class__.__name__})")
    source_health.append(
        llm_cost.coverage_record(
            "UC registered models",
            uc_status,
            source_key="ai-catalog-databricks-uc",
            source_type="inventory",
            freshness="on sync",
            retention_days=None,
            row_count=uc_rows,
            available_metrics=["models", "grants"],
            notes=uc_note,
        )
    )

    try:
        endpoints = ml.fetch_serving_endpoints(w)
        acls, acl_errors = ai_catalog.fetch_endpoint_acls(w, endpoints)
        catalog_rows.extend(ai_catalog.normalize_serving_entities(endpoints))
        access_rows.extend(ai_catalog.normalize_endpoint_acls(acls))
        refreshed.append("databricks_serving")
        serving_status = "partial" if acl_errors else "available"
        serving_note = "Serving endpoints, served entities and workspace ACLs"
        if acl_errors:
            serving_note += f"; {acl_errors} endpoints had unreadable ACLs"
        serving_rows = len(endpoints)
    except Exception as error:  # noqa: BLE001 - one surface never kills the sync
        serving_status, serving_rows = "unavailable", 0
        serving_note = f"Serving endpoint listing unavailable: {error.__class__.__name__}"
        notes.append(f"databricks_serving unavailable ({error.__class__.__name__})")
    source_health.append(
        llm_cost.coverage_record(
            "Model serving endpoints",
            serving_status,
            source_key="ai-catalog-databricks-serving",
            source_type="inventory",
            freshness="on sync",
            retention_days=None,
            row_count=serving_rows,
            available_metrics=["served_entities", "acls"],
            notes=serving_note,
        )
    )

    subscriptions = ai_catalog.parse_subscriptions(
        args.subscriptions
        if args.subscriptions is not None
        else s.ai_catalog_subscriptions
    )
    scope_note = (
        f"subscriptions: {', '.join(subscriptions)}"
        if subscriptions
        else "all subscriptions visible to the identity"
    )
    try:
        cred = secrets.get_credential(args.service_credential or None)
        accounts = ai_catalog.fetch_resource_graph(
            cred, ai_catalog.AI_ACCOUNTS_QUERY, subscriptions
        )
        deployments = ai_catalog.fetch_resource_graph(
            cred, ai_catalog.AI_DEPLOYMENTS_QUERY, subscriptions
        )
        azure_status = "available"
        azure_note = scope_note
        if accounts and not deployments:
            deployments = ai_catalog.fetch_deployments_via_arm(
                cred, [str(a.get("id") or "").lower() for a in accounts]
            )
            azure_status = "partial"
            azure_note += "; deployments listed via ARM fallback (absent from Resource Graph)"
        assignments = ai_catalog.fetch_resource_graph(
            cred,
            ai_catalog.AI_ROLE_ASSIGNMENTS_QUERY,
            subscriptions,
            authorization_scoped=True,
        )
        accounts_by_id = {str(a.get("id") or "").lower(): a for a in accounts}
        catalog_rows.extend(ai_catalog.normalize_azure_accounts(accounts))
        catalog_rows.extend(
            ai_catalog.normalize_azure_deployments(deployments, accounts_by_id)
        )
        access_rows.extend(
            ai_catalog.match_assignments_to_accounts(assignments, accounts)
        )
        refreshed.append("azure_openai")
        azure_rows = len(accounts) + len(deployments)
    except Exception as error:  # noqa: BLE001 - Azure integration is optional
        azure_status, azure_rows = "unavailable", 0
        azure_note = (
            f"Azure Resource Graph unavailable ({error.__class__.__name__}); {scope_note}"
        )
        notes.append(f"azure_openai unavailable ({error.__class__.__name__})")
    source_health.append(
        llm_cost.coverage_record(
            "Azure AI resources (Resource Graph)",
            azure_status,
            source_key="ai-catalog-azure-arg",
            source_type="inventory",
            freshness="on sync",
            retention_days=None,
            row_count=azure_rows,
            available_metrics=["accounts", "deployments", "role_assignments"],
            notes=azure_note,
        )
    )

    findings = {
        key: rows
        for key, rows in ai_catalog.classify_ai_catalog(catalog_rows, access_rows).items()
        if ai_catalog.CHECK_SOURCES[key] in refreshed
    }
    counts = [{"check": k, "findings": len(v)} for k, v in sorted(findings.items())]

    if args.no_store:
        emit(args, "AI catalog sync (preview, not stored)", counts, notes)
        return 0
    if not refreshed:
        raise RuntimeError(
            "No catalog source refreshed; canonical snapshot left unchanged."
        )
    stored_models = ai_catalog.store_catalog(
        w,
        warehouse,
        s.dashboard_catalog,
        s.dashboard_schema,
        catalog_rows,
        workspace_id=workspace_id,
        environment=environment,
        sources=refreshed,
    )
    stored_access = ai_catalog.store_access(
        w,
        warehouse,
        s.dashboard_catalog,
        s.dashboard_schema,
        access_rows,
        workspace_id=workspace_id,
        environment=environment,
        sources=refreshed,
    )
    stored_findings = 0
    if findings:
        stored_findings = digest.store_findings(
            w,
            warehouse,
            s.dashboard_catalog,
            s.dashboard_schema,
            findings,
            workspace_id=workspace_id,
            environment=environment,
        )
    health_rows = llm_cost.store_source_health(
        w,
        warehouse,
        s.dashboard_catalog,
        s.dashboard_schema,
        source_health,
        workspace_id=workspace_id,
        environment=environment,
    )
    emit(
        args,
        "AI catalog sync",
        [
            {
                "model_rows": stored_models,
                "access_rows": stored_access,
                "finding_rows": stored_findings,
                "source_health_rows": health_rows,
                "catalog_table": (
                    f"{s.dashboard_catalog}.{s.dashboard_schema}.ai_model_catalog"
                ),
                "access_table": (
                    f"{s.dashboard_catalog}.{s.dashboard_schema}.ai_model_access"
                ),
            }
        ],
        notes,
    )
    emit(args, "AI catalog findings", counts)
    return 0


def cmd_ai_catalog_report(args) -> int:
    from dbx_platform import ai_catalog

    s = Settings.from_env()
    w = get_client(args.profile)
    rows = ai_catalog.read_catalog(
        w,
        _warehouse_id(args, s),
        s.dashboard_catalog,
        s.dashboard_schema,
        str(w.get_workspace_id()),
        s.environment,
        source=args.source,
    )
    scope = args.source or "all sources"
    emit(args, f"AI model catalog — {scope}", rows)
    return 0


def cmd_ai_catalog_access(args) -> int:
    from dbx_platform import ai_catalog

    s = Settings.from_env()
    w = get_client(args.profile)
    rows = ai_catalog.read_access(
        w,
        _warehouse_id(args, s),
        s.dashboard_catalog,
        s.dashboard_schema,
        str(w.get_workspace_id()),
        s.environment,
        model_key=args.model_key,
        principal=args.principal,
    )
    emit(args, "AI model access — who can reach which model", rows)
    return 0


# --- ai-monitor --------------------------------------------------------------------


def cmd_ai_monitor_rollup(args) -> int:
    """Roll per-request serving telemetry up to daily per-endpoint/app rows."""

    from datetime import date, timedelta

    s = Settings.from_env()
    w = get_client(args.profile)
    if not args.no_store and not _verify_governed_write(args, w, s):
        return 2
    from dbx_platform import ai_monitor, digest, llm_cost

    warehouse = _warehouse_id(args, s)
    workspace_id = str(w.get_workspace_id())
    environment = getattr(args, "environment", s.environment)
    days = min(args.days if args.days is not None else 7, 90)
    window_end = date.today()
    window_start = window_end - timedelta(days=days)
    notes: list[str] = []
    rows: list[dict] = []
    refreshed: list[str] = []
    source_health: list[dict] = []

    try:
        endpoint_rows = ai_monitor.endpoint_usage_daily(w, warehouse, days)
        rows.extend(endpoint_rows)
        refreshed.append(ai_monitor.ENDPOINT_USAGE_SOURCE)
        endpoint_status = "available"
        endpoint_note = "Per-request usage for endpoints with usage tracking enabled"
    except Exception as error:  # noqa: BLE001 - feature detection
        endpoint_rows = []
        endpoint_status = "unavailable"
        endpoint_note = f"Serving usage unavailable: {error.__class__.__name__}"
        notes.append(f"system.serving.endpoint_usage unavailable ({error.__class__.__name__})")
    source_health.append(
        llm_cost.coverage_record(
            "Serving endpoint usage",
            endpoint_status,
            source_key="ai-monitor-endpoint-usage",
            source_type="usage",
            freshness="throughout the day",
            retention_days=90,
            coverage_start=(
                window_start.isoformat() if endpoint_status != "unavailable" else None
            ),
            coverage_end=(
                window_end.isoformat() if endpoint_status != "unavailable" else None
            ),
            row_count=len(endpoint_rows),
            available_metrics=[
                "requests", "errors", "input_tokens", "output_tokens",
                "distinct_requesters",
            ],
            notes=endpoint_note,
        )
    )

    try:
        gateway_rows = ai_monitor.gateway_usage_daily(w, warehouse, days)
        rows.extend(gateway_rows)
        refreshed.append(ai_monitor.GATEWAY_USAGE_SOURCE)
        gateway_status = "available"
        gateway_note = "Beta; adds latency and request-tag attribution"
    except Exception as error:  # noqa: BLE001 - optional Beta source
        gateway_rows = []
        gateway_status = "unavailable"
        gateway_note = f"Optional Beta source: {error.__class__.__name__}"
        notes.append(f"system.ai_gateway.usage unavailable ({error.__class__.__name__})")
    source_health.append(
        llm_cost.coverage_record(
            "Unity AI Gateway usage",
            gateway_status,
            source_key="ai-monitor-ai-gateway",
            source_type="usage",
            freshness="throughout the day",
            retention_days=365,
            coverage_start=(
                window_start.isoformat() if gateway_status != "unavailable" else None
            ),
            coverage_end=(
                window_end.isoformat() if gateway_status != "unavailable" else None
            ),
            row_count=len(gateway_rows),
            available_metrics=["requests", "errors", "p95_latency_ms"],
            notes=gateway_note,
        )
    )

    findings: dict[str, list[dict]] = {}
    if ai_monitor.ENDPOINT_USAGE_SOURCE in refreshed:
        try:
            endpoints = ml.fetch_serving_endpoints(w)
        except Exception as error:  # noqa: BLE001 - degrade the config-joined checks
            endpoints = []
            notes.append(f"endpoint listing unavailable ({error.__class__.__name__})")
        try:
            cost_rows = ml.serving_cost(w, warehouse, days)
        except Exception as error:  # noqa: BLE001 - degrade the tracking-gap check
            cost_rows = []
            notes.append(f"serving cost unavailable ({error.__class__.__name__})")
        findings = ai_monitor.classify_ai_monitoring(
            rows,
            endpoints,
            cost_rows,
            _now_ms(),
            spike_pct=s.ai_error_spike_pct,
            min_requests=s.ai_error_min_requests,
            min_error_rate_pct=s.ai_error_min_rate_pct,
            stale_days=s.serving_stale_days,
        )
    else:
        notes.append("findings skipped: the serving usage source did not refresh")
    counts = [{"check": k, "findings": len(v)} for k, v in sorted(findings.items())]

    if args.no_store:
        emit(args, "AI monitoring rollup (preview, not stored)", counts, notes)
        return 0
    if not refreshed:
        raise RuntimeError("No usage source refreshed; canonical rollup left unchanged.")
    stored = ai_monitor.store_monitoring(
        w,
        warehouse,
        s.dashboard_catalog,
        s.dashboard_schema,
        rows,
        workspace_id=workspace_id,
        environment=environment,
        window_start=window_start.isoformat(),
        window_end=window_end.isoformat(),
        sources=refreshed,
    )
    stored_findings = 0
    if findings:
        stored_findings = digest.store_findings(
            w,
            warehouse,
            s.dashboard_catalog,
            s.dashboard_schema,
            findings,
            workspace_id=workspace_id,
            environment=environment,
        )
    health_rows = llm_cost.store_source_health(
        w,
        warehouse,
        s.dashboard_catalog,
        s.dashboard_schema,
        source_health,
        workspace_id=workspace_id,
        environment=environment,
    )
    emit(
        args,
        f"AI monitoring rollup — last {days}d",
        [
            {
                "usage_rows": stored,
                "finding_rows": stored_findings,
                "source_health_rows": health_rows,
                "table": (
                    f"{s.dashboard_catalog}.{s.dashboard_schema}.ai_app_monitoring"
                ),
            }
        ],
        notes,
    )
    emit(args, "AI monitoring findings", counts)
    return 0


def cmd_ai_monitor_report(args) -> int:
    from dbx_platform import ai_monitor

    s = Settings.from_env()
    w = get_client(args.profile)
    days = args.days if args.days is not None else s.lookback_days
    rows = ai_monitor.report(
        w,
        _warehouse_id(args, s),
        s.dashboard_catalog,
        s.dashboard_schema,
        str(w.get_workspace_id()),
        s.environment,
        days,
    )
    emit(args, f"AI app usage by app/endpoint — last {days}d", rows)
    return 0


# --- dashboards --------------------------------------------------------------------


def cmd_dashboards_render(args) -> int:
    from dbx_platform import dashboards

    s = Settings.from_env()
    catalog = args.catalog or s.dashboard_catalog
    schema = args.schema or s.dashboard_schema
    written = dashboards.render_all(args.dashboards_dir, catalog, schema)
    for p in written:
        print(f"rendered: {p} (helper objects in {catalog}.{schema})")
    print("Commit the rendered files, then deploy with: databricks bundle deploy")
    return 0


def cmd_dashboards_setup(args) -> int:
    return _reject_direct_state_change(
        "dashboard schema setup; run the bundle's schema_migrations bootstrap job"
    )


def cmd_dashboards_health(args) -> int:
    from dbx_platform import dashboards

    s = Settings.from_env()
    catalog = args.catalog or s.dashboard_catalog
    schema = args.schema or s.dashboard_schema
    w = get_client(args.profile)
    rows = dashboards.dependency_health(
        w,
        _warehouse_id(args, s),
        catalog,
        schema,
    )
    emit(args, f"Dashboard dependencies — {catalog}.{schema}", rows)
    return 0


# --- report ------------------------------------------------------------------------


def cmd_report_operational_findings(args) -> int:
    """Collect deterministic report-only performance and UC security signals."""

    s = Settings.from_env()
    w = get_client(args.profile)
    if not args.no_store and not _verify_governed_write(args, w, s):
        return 2
    from dbx_platform import digest, operational

    warehouse = _warehouse_id(args, s)
    workspace_id = str(w.get_workspace_id())
    findings, coverage = operational.collect_findings(
        w,
        warehouse,
        catalog=s.dashboard_catalog,
        schema=s.dashboard_schema,
        workspace_id=workspace_id,
        environment=args.environment,
        recent_days=args.recent_days,
        baseline_days=args.baseline_days,
        limit=args.limit,
        now_ms=_now_ms(),
        token_max_age_days=s.token_max_age_days,
        token_expiry_warn_days=s.token_expiry_warn_days,
        inactive_user_days=s.inactive_user_days,
    )
    counts = [
        {"check": check, "findings": len(rows)}
        for check, rows in sorted(findings.items())
    ]
    emit(
        args,
        "Operational findings (report only)",
        counts,
        [
            "No resource changes or dollar-impact estimates are made by this v1 pack.",
            "UNAVAILABLE/UNSUPPORTED checks are omitted from the canonical refresh so "
            "prior findings are preserved.",
        ],
    )
    emit(args, "Operational source coverage", coverage)
    if args.no_store:
        return 0
    if not findings:
        raise RuntimeError(
            "No operational source refreshed; canonical findings were left unchanged."
        )
    stored = digest.store_findings(
        w,
        warehouse,
        s.dashboard_catalog,
        s.dashboard_schema,
        findings,
        workspace_id=workspace_id,
        environment=args.environment,
    )
    emit(
        args,
        "Operational findings stored",
        [
            {
                "rows": stored,
                "table": (
                    f"{s.dashboard_catalog}.{s.dashboard_schema}.platform_findings"
                ),
            }
        ],
    )
    return 0


def cmd_report_impact_followup(args) -> int:
    """Measure post-window outcomes without inventing unsupported savings."""

    s = Settings.from_env()
    w = get_client(args.profile)
    if not _verify_governed_write(args, w, s):
        return 2
    from dbx_platform import impact_measurement

    measured = impact_measurement.measure_due_actions(
        w,
        _warehouse_id(args, s),
        catalog=s.dashboard_catalog,
        schema=s.dashboard_schema,
        workspace_id=str(w.get_workspace_id()),
        environment=args.environment,
        limit=args.limit,
    )
    emit(
        args,
        "Action impact follow-up",
        measured,
        [
            "Financial, risk, and performance outcomes are compared only where "
            "canonical sources can attribute them to exact targets.",
            "Unavailable attribution remains explicit and is never estimated.",
        ],
    )
    return 0


def cmd_report_ai_digest(args) -> int:
    s = Settings.from_env()
    w = get_client(args.profile)
    if not _verify_governed_write(args, w, s):
        return 2
    from dbx_platform import digest

    days = args.days if args.days is not None else s.lookback_days
    model = args.model or s.digest_model
    warehouse = _warehouse_id(args, s)
    findings, skipped = digest.collect_findings(w, s, warehouse, _now_ms(), days)
    counts = [{"check": k, "findings": len(v)} for k, v in sorted(findings.items())]
    notes = [f"skipped {k}: {v}" for k, v in sorted(skipped.items())]
    emit(args, f"Digest inputs — last {days}d", counts, notes)
    prompt = digest.build_digest_prompt(findings, skipped, days)
    try:
        summary = digest.summarize(w, warehouse, model, prompt)
        emit(args, f"AI digest ({model})", [{"digest": summary}])
    except (SystemTablesUnavailableError, RuntimeError, ValueError) as e:
        summary = ""
        emit(args, "AI digest", [], [f"skipped: ai summary unavailable ({e})"])
    if not args.no_store:
        try:
            digest.store_digest(
                w,
                warehouse,
                s.dashboard_catalog,
                s.dashboard_schema,
                days,
                model,
                summary,
                findings,
                workspace_id=str(w.get_workspace_id()),
                environment=args.environment,
            )
            print(
                f"  stored: {s.dashboard_catalog}.{s.dashboard_schema}."
                "platform_digest/platform_findings"
            )
        except (SystemTablesUnavailableError, RuntimeError, ValueError) as e:
            print(f"  note: not stored ({e}) — run the deployment schema_migrations job first.")
    return 0


# --- release ----------------------------------------------------------------------


def cmd_publish_wheel(args) -> int:
    return _reject_direct_state_change("wheel publication to a Unity Catalog Volume")


# --- parser ------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--profile", help="Profile name in ~/.databrickscfg (default: unified auth)"
    )
    common.add_argument("--warehouse-id", help="SQL warehouse for system-table queries")
    common.add_argument("--output", choices=["table", "json"], default="table")

    mutating = argparse.ArgumentParser(add_help=False)
    mutating.add_argument(
        "--apply", action="store_true", help="Removed: use a Mission Control approved action"
    )
    mutating.add_argument(
        "--yes", action="store_true", help="Deprecated compatibility flag; never authorizes changes"
    )

    governed_write = argparse.ArgumentParser(add_help=False)
    governed_write.add_argument("--approved-action-id", default="")
    governed_write.add_argument("--approved-plan-hash", default="")
    governed_write.add_argument("--approved-job-id", type=int, default=0)
    governed_write.add_argument("--approved-run-id", type=int, default=0)
    governed_write.add_argument("--trigger-type", default="")
    governed_write.add_argument("--environment", default="prod")

    p = argparse.ArgumentParser(
        prog="dbx-platform", description="Databricks platform management toolkit"
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="area")

    # cost
    pc = sub.add_parser("cost", help="Cost & usage monitoring").add_subparsers(dest="command")
    x = pc.add_parser("report", parents=[common], help="DBU/$ by SKU and workspace")
    x.add_argument("--days", type=int, default=None)
    x.set_defaults(func=cmd_cost_report)
    x = pc.add_parser("top-jobs", parents=[common], help="Most expensive jobs")
    x.add_argument("--days", type=int, default=None)
    x.add_argument("--limit", type=int, default=20)
    x.set_defaults(func=cmd_cost_top_jobs)
    x = pc.add_parser(
        "attribution",
        parents=[common],
        help="List cost by enforced team/project tag (or whole workspace)",
    )
    x.add_argument("--days", type=int, default=None)
    x.add_argument(
        "--dimension", choices=sorted(cost.ATTRIBUTION_DIMENSIONS), default="team"
    )
    x.set_defaults(func=cmd_cost_attribution)
    x = pc.add_parser(
        "cluster-utilization",
        parents=[common, governed_write],
        help="Under-utilized clusters (CPU/memory vs size, by cost)",
    )
    x.add_argument("--days", type=int, default=None)
    x.add_argument("--cpu-threshold", type=int, default=None, help="p95 CPU %% floor")
    x.add_argument("--mem-threshold", type=int, default=None, help="avg memory %% floor")
    x.add_argument(
        "--store-findings",
        action="store_true",
        help="Persist findings to platform_findings (requires a governed Job context)",
    )
    x.set_defaults(func=cmd_cost_cluster_utilization)
    x = pc.add_parser(
        "failed-run-waste", parents=[common], help="$ burned on failed/timed-out job runs"
    )
    x.add_argument("--days", type=int, default=None)
    x.add_argument("--limit", type=int, default=20)
    x.set_defaults(func=cmd_cost_failed_run_waste)
    x = pc.add_parser(
        "warehouse-utilization",
        parents=[common, governed_write],
        help="SQL warehouses: idle spend or sustained queueing",
    )
    x.add_argument("--days", type=int, default=None)
    x.add_argument(
        "--store-findings",
        action="store_true",
        help="Persist findings to platform_findings (requires a governed Job context)",
    )
    x.set_defaults(func=cmd_cost_warehouse_utilization)

    # llm-cost
    pl = sub.add_parser(
        "llm-cost",
        help="Normalize LLM cost, tokens and performance into Unity Catalog",
    ).add_subparsers(dest="command")
    x = pl.add_parser(
        "rollup",
        parents=[common, governed_write],
        help="Upsert Databricks, AI Gateway and Azure AI cost/usage ledgers",
    )
    x.add_argument(
        "--days",
        type=int,
        default=None,
        help="Window to reprocess; default 3 days, use 400 for a daily backfill",
    )
    x.set_defaults(func=cmd_llm_cost_rollup)

    # azure-cost
    pa = sub.add_parser(
        "azure-cost", help="Azure bill: ingest + report (Cost Management API)"
    ).add_subparsers(dest="command")
    x = pa.add_parser(
        "pull",
        parents=[common, governed_write],
        help="Pull the Azure bill into <catalog>.<schema>.azure_costs",
    )
    x.add_argument(
        "--days",
        type=int,
        default=None,
        help="Window to (re-)pull, default 3 (use 365 to backfill)",
    )
    x.add_argument(
        "--subscription-id",
        default=None,
        help="Azure subscription (default: DBX_PLATFORM_AZURE_SUBSCRIPTION_ID)",
    )
    x.add_argument(
        "--service-credential",
        default=None,
        help="UC service credential name for keyless Azure auth",
    )
    x.set_defaults(func=cmd_azure_cost_pull)
    x = pa.add_parser("report", parents=[common], help="Azure spend from the ingested bill")
    x.add_argument("--days", type=int, default=None)
    x.add_argument("--by", choices=["bucket", "service", "resource-group"], default="bucket")
    x.set_defaults(func=cmd_azure_cost_report)
    x = pa.add_parser(
        "spikes",
        parents=[common, governed_write],
        help="Per-bucket day-over-trailing-week spend spikes",
    )
    x.add_argument("--days", type=int, default=None)
    x.add_argument(
        "--store-findings",
        action="store_true",
        help="Persist findings to platform_findings (requires a governed Job context)",
    )
    x.set_defaults(func=cmd_azure_cost_spikes)
    x = pa.add_parser(
        "detail",
        parents=[common],
        help="Detail-grain Azure spend (per resource/meter) from azure_cost_details",
    )
    x.add_argument("--days", type=int, default=None)
    x.add_argument("--by", choices=["resource", "meter", "resource-group"], default="meter")
    x.add_argument(
        "--bucket",
        choices=["databricks", "foundry_ai", "search", "storage", "other"],
        default=None,
        help="Restrict to one allocation bucket (e.g. foundry_ai)",
    )
    x.set_defaults(func=cmd_azure_cost_detail)

    # estimator
    pe = sub.add_parser(
        "estimator", help="AI solution cost & TCO estimates from versioned price snapshots"
    ).add_subparsers(dest="command")
    x = pe.add_parser(
        "prices-pull",
        parents=[common, governed_write],
        help="Snapshot Azure Retail Prices + $/DBU list prices into "
        "estimator_price_snapshots",
    )
    x.add_argument("--region", default=None, help="Azure region (default eastus)")
    x.add_argument("--currency", default=None, help="Currency code (default USD)")
    x.add_argument(
        "--snapshot-date", default=None, help="Snapshot date override (default today)"
    )
    x.set_defaults(func=cmd_estimator_prices_pull)
    x = pe.add_parser(
        "prices-status", parents=[common, governed_write],
        help="Freshness and coverage of the stored price snapshot",
    )
    x.set_defaults(func=cmd_estimator_prices_status)
    x = pe.add_parser(
        "patterns", parents=[common], help="List the plain-English solution patterns"
    )
    x.set_defaults(func=cmd_estimator_patterns)
    x = pe.add_parser(
        "prompts-sync",
        parents=[common],
        help="Register the wheel's extraction prompts in the UC prompt registry "
        "(deployment-run; skips unchanged content hashes)",
    )
    x.set_defaults(func=cmd_estimator_prompts_sync)
    x = pe.add_parser(
        "eval-extraction",
        parents=[common],
        help="Run the golden extraction dataset against a serving endpoint and "
        "log code-scorer metrics to MLflow",
    )
    x.add_argument("--endpoint", default=None, help="Serving endpoint (default digest model)")
    x.add_argument(
        "--experiment",
        default="/Shared/dbx-platform/estimator-extraction-eval",
        help="MLflow experiment path or ID",
    )
    x.add_argument(
        "--min-pattern-accuracy",
        type=float,
        default=0.8,
        help="Exit nonzero below this pattern accuracy (the prompt-change gate)",
    )
    x.set_defaults(func=cmd_estimator_eval_extraction)
    x = pe.add_parser(
        "estimate",
        parents=[common, governed_write],
        help="Compute a 3-tier TCO matrix from a requirements JSON file",
    )
    x.add_argument(
        "--requirements-file", required=True, help="Path to a requirements JSON document"
    )
    x.add_argument(
        "--rigor", type=int, default=10, help="Production review coverage %% (0-100)"
    )
    x.set_defaults(func=cmd_estimator_estimate)

    # forecast
    pf = sub.add_parser(
        "forecast", help="ML cost forecasting: features, train, predict, monitor"
    ).add_subparsers(dest="command")
    x = pf.add_parser(
        "build-features",
        parents=[common, governed_write],
        help="Engineer lag/rolling/calendar features from azure_costs",
    )
    x.add_argument("--days", type=int, default=None, help="History window (default 365)")
    x.set_defaults(func=cmd_forecast_build_features)
    x = pf.add_parser(
        "train",
        parents=[common, governed_write],
        help="Backtest candidates, register + gate @champion (needs dbx-platform[forecast])",
    )
    x.add_argument("--folds", type=int, default=4)
    x.add_argument("--horizon", type=int, default=14)
    x.add_argument(
        "--min-improvement",
        type=float,
        default=0.01,
        help="Relative WAPE margin the challenger must win by",
    )
    x.add_argument(
        "--no-promote",
        action="store_true",
        help="Preview: backtest + register + @challenger, but report "
        "the gate's decision instead of moving @champion",
    )
    x.add_argument("--model-name", default=None)
    x.add_argument("--experiment", default=None)
    x.set_defaults(func=cmd_forecast_train)
    x = pf.add_parser(
        "predict",
        parents=[common, governed_write],
        help="Batch inference from @champion into cost_forecasts",
    )
    x.add_argument("--horizon", type=int, default=None)
    x.add_argument("--model-name", default=None)
    x.set_defaults(func=cmd_forecast_predict)
    x = pf.add_parser(
        "monitor",
        parents=[common, governed_write],
        help="PSI feature drift + matured-forecast accuracy; exits 1 on retrain verdict",
    )
    x.add_argument(
        "--no-store", action="store_true", help="Skip writing findings to platform_findings"
    )
    x.set_defaults(func=cmd_forecast_monitor)
    x = pf.add_parser("status", parents=[common], help="Champion/challenger registry status")
    x.add_argument("--model-name", default=None)
    x.set_defaults(func=cmd_forecast_status)

    # housekeeping
    ph = sub.add_parser("housekeeping", help="Cleanup reports").add_subparsers(dest="command")
    x = ph.add_parser(
        "stale-clusters", parents=[common, mutating], help="Stale / long-running clusters"
    )
    x.add_argument("--stale-days", type=int, default=None)
    x.add_argument("--max-uptime-hours", type=int, default=None)
    x.set_defaults(func=cmd_stale_clusters)
    x = ph.add_parser(
        "orphaned-jobs", parents=[common, mutating], help="Jobs owned by missing principals"
    )
    x.set_defaults(func=cmd_orphaned_jobs)
    x = ph.add_parser(
        "jobs-on-all-purpose",
        parents=[common],
        help="Jobs paying the all-purpose premium or pinning large fixed clusters",
    )
    x.set_defaults(func=cmd_jobs_on_all_purpose)

    # security
    ps = sub.add_parser("security", help="Security & audit").add_subparsers(dest="command")
    x = ps.add_parser("token-audit", parents=[common, mutating], help="PAT age/expiry audit")
    x.add_argument("--max-age-days", type=int, default=None)
    x.set_defaults(func=cmd_token_audit)
    x = ps.add_parser("inactive-users", parents=[common], help="Users with no recent activity")
    x.add_argument("--days", type=int, default=None)
    x.set_defaults(func=cmd_inactive_users)

    # governance
    pg = sub.add_parser("governance", help="Policies & tags").add_subparsers(dest="command")
    x = pg.add_parser(
        "policy-sync",
        parents=[common, mutating],
        help="Diff/apply cluster policies from policies/*.json",
    )
    x.add_argument("--policies-dir", default="policies")
    x.set_defaults(func=cmd_policy_sync)
    x = pg.add_parser("tag-compliance", parents=[common], help="Missing tags + untagged spend")
    x.add_argument("--required-tags", default=None, help="Comma-separated tag keys")
    x.add_argument("--days", type=int, default=None)
    x.set_defaults(func=cmd_tag_compliance)
    x = pg.add_parser(
        "tag-recommendations",
        parents=[common],
        help="Suggest fixes for missing tags (typo/near-match + inferred values)",
    )
    x.add_argument("--required-tags", default=None, help="Comma-separated tag keys")
    x.set_defaults(func=cmd_tag_recommendations)

    # ml
    pm = sub.add_parser(
        "ml", help="AI/ML workloads: serving, models, GPU, vector search"
    ).add_subparsers(dest="command")
    x = pm.add_parser(
        "endpoint-audit",
        parents=[common],
        help="Serving endpoint hygiene: state, scale-to-zero, inference tables, AI Gateway",
    )
    x.add_argument("--stale-days", type=int, default=None)
    x.set_defaults(func=cmd_ml_endpoint_audit)
    x = pm.add_parser(
        "model-hygiene",
        parents=[common],
        help="UC registered models: stale, ownerless, unaliased, never served",
    )
    x.add_argument("--catalog", default=None, help="Limit to one catalog")
    x.add_argument("--schema", default=None, help="Limit to one schema (needs --catalog)")
    x.add_argument("--stale-days", type=int, default=None)
    x.add_argument("--unaliased-days", type=int, default=None)
    x.set_defaults(func=cmd_ml_model_hygiene)
    x = pm.add_parser(
        "serving-cost", parents=[common], help="Serving/vector-search/AI spend and token usage"
    )
    x.add_argument("--days", type=int, default=None)
    x.set_defaults(func=cmd_ml_serving_cost)
    x = pm.add_parser(
        "gpu-audit", parents=[common], help="Interactive GPU clusters + GPU spend share"
    )
    x.add_argument("--max-uptime-hours", type=int, default=None)
    x.add_argument("--days", type=int, default=None)
    x.set_defaults(func=cmd_ml_gpu_audit)
    x = pm.add_parser(
        "vector-search-audit",
        parents=[common],
        help="Vector search endpoints: no indexes / unhealthy",
    )
    x.add_argument(
        "--grace-minutes",
        type=int,
        default=None,
        help="Minimum endpoint age before idle findings; overrides env grace hours",
    )
    x.set_defaults(func=cmd_ml_vector_search_audit)

    # ai-catalog
    pac = sub.add_parser(
        "ai-catalog",
        help="Unified AI model catalog: models + identities (Databricks + Azure)",
    ).add_subparsers(dest="command")
    x = pac.add_parser(
        "sync",
        parents=[common, governed_write],
        help="Snapshot models and access into ai_model_catalog/ai_model_access",
    )
    x.add_argument(
        "--service-credential",
        default=None,
        help="UC service credential name for keyless Azure auth",
    )
    x.add_argument(
        "--subscriptions",
        default=None,
        help="Comma-separated Azure subscription IDs to inventory "
             "(default: DBX_PLATFORM_AI_CATALOG_SUBSCRIPTIONS; empty = all "
             "subscriptions the identity can read)",
    )
    x.add_argument(
        "--no-store",
        action="store_true",
        help="Read-only preview; skip table writes and findings",
    )
    x.set_defaults(func=cmd_ai_catalog_sync)
    x = pac.add_parser("report", parents=[common], help="Cataloged models by source")
    x.add_argument(
        "--source",
        default=None,
        choices=["databricks_uc", "databricks_serving", "azure_openai"],
    )
    x.set_defaults(func=cmd_ai_catalog_report)
    x = pac.add_parser("access", parents=[common], help="Who can access which model")
    x.add_argument("--model-key", default=None, help="Filter to one model_key")
    x.add_argument("--principal", default=None, help="Filter to one principal")
    x.set_defaults(func=cmd_ai_catalog_access)

    # ai-monitor
    pam = sub.add_parser(
        "ai-monitor",
        help="Production AI app monitoring from serving system tables",
    ).add_subparsers(dest="command")
    x = pam.add_parser(
        "rollup",
        parents=[common, governed_write],
        help="Roll up per-day endpoint/app usage into ai_app_monitoring",
    )
    x.add_argument(
        "--days",
        type=int,
        default=None,
        help="Window to reprocess; default 7, capped at the 90d source retention",
    )
    x.add_argument(
        "--no-store",
        action="store_true",
        help="Read-only preview; skip table writes and findings",
    )
    x.set_defaults(func=cmd_ai_monitor_rollup)
    x = pam.add_parser("report", parents=[common], help="Per-app usage and error summary")
    x.add_argument("--days", type=int, default=None)
    x.set_defaults(func=cmd_ai_monitor_report)

    # dashboards
    pd = sub.add_parser("dashboards", help="AI/BI dashboards").add_subparsers(dest="command")
    x = pd.add_parser(
        "render", parents=[common], help="Render dashboards/templates -> dashboards/*.lvdash.json"
    )
    x.add_argument("--catalog", default=None, help="Catalog for helper objects (default: main)")
    x.add_argument(
        "--schema", default=None, help="Schema for helper objects (default: dbx_platform)"
    )
    x.add_argument("--dashboards-dir", default="dashboards")
    x.set_defaults(func=cmd_dashboards_render)
    x = pd.add_parser(
        "setup", parents=[common], help="Disabled compatibility command; use schema_migrations"
    )
    x.add_argument("--catalog", default=None)
    x.add_argument("--schema", default=None)
    x.add_argument(
        "--team-tags",
        default=None,
        help="Comma-separated tag keys used to derive team names (default: required tags)",
    )
    x.add_argument(
        "--workspace-name",
        default=None,
        help="Friendly name for the current workspace in cost dashboards",
    )
    x.set_defaults(func=cmd_dashboards_setup)
    x = pd.add_parser(
        "health",
        parents=[common],
        help="Read-only availability check for dashboard helper objects",
    )
    x.add_argument("--catalog", default=None)
    x.add_argument("--schema", default=None)
    x.set_defaults(func=cmd_dashboards_health)

    # report
    pp = sub.add_parser("report", help="Cross-area reports").add_subparsers(dest="command")
    x = pp.add_parser(
        "operational-findings",
        parents=[common, governed_write],
        help=(
            "Deterministic job, query, serving, and bounded UC security findings "
            "(report only)"
        ),
    )
    x.add_argument("--recent-days", type=int, default=7)
    x.add_argument("--baseline-days", type=int, default=28)
    x.add_argument("--limit", type=int, default=100)
    x.add_argument(
        "--no-store",
        action="store_true",
        help="Read-only preview; do not update canonical platform_findings",
    )
    x.set_defaults(func=cmd_report_operational_findings)
    x = pp.add_parser(
        "impact-followup",
        parents=[common, governed_write],
        help="Measure due expected-versus-realized action outcomes",
    )
    x.add_argument("--limit", type=int, default=100)
    x.set_defaults(func=cmd_report_impact_followup)
    x = pp.add_parser(
        "ai-digest",
        parents=[common, governed_write],
        help="AI-summarized digest of all checks (ai_query on the warehouse)",
    )
    x.add_argument("--days", type=int, default=None)
    x.add_argument(
        "--model", default=None, help="Foundation-model serving endpoint (default: settings)"
    )
    x.add_argument(
        "--no-store",
        action="store_true",
        help="Skip writing to the platform_digest/platform_findings tables",
    )
    x.set_defaults(func=cmd_report_ai_digest)

    # release
    pr = sub.add_parser("release", help="Distribution helpers").add_subparsers(dest="command")
    x = pr.add_parser(
        "publish-wheel",
        parents=[common],
        help="Disabled compatibility command; use reviewed deployment",
    )
    x.add_argument("--volume", default=None)
    x.add_argument("--wheel", default=None)
    x.set_defaults(func=cmd_publish_wheel)

    return p


_HANDLED_ERRORS = (SystemTablesUnavailableError, RuntimeError, ValueError,
                   FileNotFoundError, TimeoutError)


def _dispatch(argv: list[str] | None) -> int:
    """Parse and run one command, letting failures propagate to the caller."""
    args = build_parser().parse_args(argv)
    if not hasattr(args, "func"):
        build_parser().parse_args((argv or sys.argv[1:]) + ["--help"])
        return 2
    return args.func(args)


def main(argv: list[str] | None = None) -> int:
    try:
        return _dispatch(argv)
    except _HANDLED_ERRORS as e:
        print(f"error: {e}", file=sys.stderr)
        return 3 if isinstance(e, SystemTablesUnavailableError) else 1


def entry() -> None:
    """Console-script entry point. Databricks python_wheel_task calls this
    function directly and ignores its return value — a task only fails if it
    raises — and `bundle run` relays only the exception text, not the task's
    stderr, so a bare SystemExit(1) leaves CI logs with no diagnosis. Raise
    SystemExit carrying the error message instead: the job fails loudly AND
    the reason lands in the CI log. Locally the interpreter prints a string
    SystemExit to stderr and exits 1."""
    try:
        code = _dispatch(None)
    except _HANDLED_ERRORS as e:
        raise SystemExit(f"error: {e}") from e
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    sys.exit(main())

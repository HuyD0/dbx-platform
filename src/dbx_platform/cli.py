"""dbx-platform CLI — single entry point for ad-hoc admin use and scheduled jobs.

The bundle's job tasks invoke this exact entry point (python_wheel_task with
``entry_point: dbx-platform``), so scheduled jobs exercise the same code path
you test locally.

Safety: every mutating command is dry-run by default. ``--apply`` requires
``--yes`` (or DBX_PLATFORM_CONFIRM=true for non-interactive contexts).

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
        print(json.dumps(
            {"title": title, "count": len(rows), "rows": rows, "notes": notes}, default=str
        ))
        return
    print(f"\n== {title} ({len(rows)}) ==")
    print(_render_table(rows))
    for n in notes:
        print(f"  note: {n}")


def check_apply(args) -> bool:
    """False when dry-run. Exits with code 2 if --apply lacks confirmation."""
    if not getattr(args, "apply", False):
        return False
    if args.yes or os.environ.get("DBX_PLATFORM_CONFIRM", "").lower() == "true":
        return True
    print(
        "error: --apply is destructive and needs confirmation: add --yes "
        "(or set DBX_PLATFORM_CONFIRM=true).",
        file=sys.stderr,
    )
    sys.exit(2)


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
    days = args.days if args.days is not None else s.lookback_days
    cpu = args.cpu_threshold if args.cpu_threshold is not None else s.util_cpu_threshold_pct
    mem = args.mem_threshold if args.mem_threshold is not None else s.util_mem_threshold_pct
    rows = cost.cluster_utilization(w, _warehouse_id(args, s), days)
    findings = cost.classify_cluster_utilization(rows, cpu, mem)
    emit(args, f"Under-utilized clusters — last {days}d (ranked by cost)", findings,
         ["Report only — right-sizing is applied by the cluster owner "
          "(see docs/runbook.md)."])
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
    days = args.days if args.days is not None else s.lookback_days
    rows = cost.warehouse_utilization(w, _warehouse_id(args, s), days)
    findings = cost.classify_warehouse_utilization(
        rows, s.warehouse_min_queries, s.warehouse_queue_warn_seconds
    )
    emit(args, f"Mis-sized SQL warehouses — last {days}d", findings,
         ["Report only — includes both directions: idle spend and sustained "
          "queueing."])
    return 0


# --- azure-cost ---------------------------------------------------------------

def cmd_azure_cost_pull(args) -> int:
    from datetime import date, timedelta

    from dbx_platform import azure_cost, secrets

    s = Settings.from_env()
    sub = args.subscription_id or s.azure_subscription_id
    days = args.days if args.days is not None else 3
    end = date.today()
    start = end - timedelta(days=days)
    cred = secrets.get_credential(args.service_credential or None)
    pages = azure_cost.fetch_cost_query(cred, sub, start.isoformat(), end.isoformat())
    rows = azure_cost.parse_query_result(pages)
    w = get_client(args.profile)
    n = azure_cost.store_costs(
        w, _warehouse_id(args, s), s.dashboard_catalog, s.dashboard_schema, rows
    )
    by_bucket: dict[str, float] = {}
    for r in rows:
        by_bucket[r["service_bucket"]] = by_bucket.get(r["service_bucket"], 0.0) + r["cost"]
    summary = [{"service_bucket": k, "cost": round(v, 2)}
               for k, v in sorted(by_bucket.items(), key=lambda kv: -kv[1])]
    emit(args, f"Azure bill pull {start}..{end} — {n} rows merged into "
               f"{s.dashboard_catalog}.{s.dashboard_schema}.azure_costs", summary)
    return 0


def cmd_azure_cost_report(args) -> int:
    from dbx_platform import azure_cost

    s = Settings.from_env()
    w = get_client(args.profile)
    days = args.days if args.days is not None else s.lookback_days
    rows = azure_cost.report(
        w, _warehouse_id(args, s), s.dashboard_catalog, s.dashboard_schema,
        args.by, days,
    )
    emit(args, f"Azure spend by {args.by} — last {days}d", rows)
    return 0


def cmd_azure_cost_spikes(args) -> int:
    from dbx_platform import azure_cost

    s = Settings.from_env()
    w = get_client(args.profile)
    days = args.days if args.days is not None else 14
    rows = azure_cost.fetch_daily_buckets(
        w, _warehouse_id(args, s), s.dashboard_catalog, s.dashboard_schema, days
    )
    findings = azure_cost.classify_azure_spend(
        rows, s.azure_spike_pct, s.azure_spike_min_cost
    )
    emit(args, "Azure spend spikes by service bucket "
               f"(day vs trailing 7d, threshold {s.azure_spike_pct}%)", findings,
         ["Report only — investigate the bucket's resources before acting."])
    return 0


# --- forecast -----------------------------------------------------------------

def cmd_forecast_build_features(args) -> int:
    from dbx_platform import azure_cost, forecast_features

    s = Settings.from_env()
    w = get_client(args.profile)
    days = args.days if args.days is not None else 365
    rows = azure_cost.fetch_daily_buckets(
        w, _warehouse_id(args, s), s.dashboard_catalog, s.dashboard_schema, days
    )
    feats = forecast_features.build_features(rows)
    n = forecast_features.store_features(
        w, _warehouse_id(args, s), s.dashboard_catalog, s.dashboard_schema, feats
    )
    series = sorted({f["series"] for f in feats})
    emit(args, f"Cost features built (set v{forecast_features.FEATURE_SET_VERSION})",
         [{"series": ", ".join(series), "rows": n,
           "table": f"{s.dashboard_catalog}.{s.dashboard_schema}.cost_features"}])
    return 0


def cmd_forecast_train(args) -> int:
    from dbx_platform import forecast_train

    s = Settings.from_env()
    w = get_client(args.profile)
    rows = forecast_train.run_training(
        w, _warehouse_id(args, s), s.dashboard_catalog, s.dashboard_schema,
        args.model_name or s.forecast_model_name,
        args.experiment or s.forecast_experiment,
        n_folds=args.folds, horizon=args.horizon,
        min_improvement=args.min_improvement,
        allow_promote=not args.no_promote,
    )
    notes = ["Batch inference resolves the model by @champion alias only."]
    if args.no_promote:
        notes.append("Preview mode — the gate's decision was reported, "
                     "@champion was not moved.")
    emit(args, "Forecast training — backtest + champion/challenger gate", rows, notes)
    return 0


def cmd_forecast_predict(args) -> int:
    from dbx_platform import forecast_infer

    s = Settings.from_env()
    w = get_client(args.profile)
    horizon = args.horizon if args.horizon is not None else s.forecast_horizon_days
    rows = forecast_infer.run_inference(
        w, _warehouse_id(args, s), s.dashboard_catalog, s.dashboard_schema,
        args.model_name or s.forecast_model_name, horizon,
    )
    emit(args, f"Azure cost forecast — next {horizon}d (P10/P50/P90)", rows)
    return 0


def cmd_forecast_monitor(args) -> int:
    from dbx_platform import forecast_monitor

    s = Settings.from_env()
    w = get_client(args.profile)
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
            print(f"  note: findings not stored ({e}) — run 'dbx-platform "
                  "dashboards setup' first.")
    if any(f["action"] == "retrain-recommended" for f in findings):
        print("retrain recommended — failing so the job notification fires.",
              file=sys.stderr)
        return 1
    return 0


def cmd_forecast_status(args) -> int:
    s = Settings.from_env()
    try:
        import mlflow
        from mlflow.tracking import MlflowClient
    except ImportError:
        print("error: forecasting libraries not installed. "
              "Run: pip install 'dbx-platform[forecast]'", file=sys.stderr)
        return 2
    mlflow.set_registry_uri("databricks-uc")
    uc_name = (f"{s.dashboard_catalog}.{s.dashboard_schema}."
               f"{args.model_name or s.forecast_model_name}")
    client = MlflowClient()
    rows = []
    for alias in ("champion", "challenger"):
        try:
            v = client.get_model_version_by_alias(uc_name, alias)
            rows.append({"alias": alias, "version": v.version,
                         "backtest_wape": v.tags.get("backtest_wape", ""),
                         "run_id": v.run_id})
        except Exception as e:  # noqa: BLE001 — absent alias is a normal state
            rows.append({"alias": alias, "version": "", "backtest_wape": "",
                         "run_id": f"(none: {e.__class__.__name__})"})
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
        notes.append("DRY RUN — re-run with --apply --yes to terminate/delete these clusters.")
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
        notes.append("DRY RUN — re-run with --apply --yes to pause schedules (never deletes).")
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
    emit(args, "Jobs on all-purpose compute / oversized fixed clusters", findings,
         ["Report only — moving a task to a job cluster is a job-spec change "
          "owned by the job's team."])
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
        notes.append("DRY RUN — re-run with --apply --yes to revoke tokens over the age limit.")
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
    emit(args, f"Users with no audited activity in {days}d", findings,
         ["Report only — deactivation stays a human/IdP decision (see docs/runbook.md)."])
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
        notes.append("DRY RUN — re-run with --apply --yes to create/update policies.")
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
    findings = governance.find_missing_tags(
        governance.fetch_taggable_resources(w), required
    )
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
    emit(args, f"Tag recommendations for {required}", recs,
         ["Suggestions only — apply tag changes manually."])
    return 0


# --- ml ----------------------------------------------------------------------------

def cmd_ml_endpoint_audit(args) -> int:
    s = Settings.from_env()
    w = get_client(args.profile)
    endpoints = ml.fetch_serving_endpoints(w)
    findings = ml.classify_serving_endpoints(endpoints, _now_ms(), s.serving_failed_grace_hours)
    emit(args, "Model serving endpoint audit", findings,
         ["Report only — endpoint config changes trigger a redeployment; apply "
          "manually (see docs/runbook.md)."])
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
    unaliased = (
        args.unaliased_days if args.unaliased_days is not None else s.model_unaliased_days
    )
    models, truncated = ml.fetch_registered_models(w, args.catalog, args.schema, s.ml_max_models)
    served = ml.served_entity_names(ml.fetch_serving_endpoints(w))
    findings = ml.classify_models(models, served, _now_ms(), stale, unaliased)
    notes = ["Report only — archiving/deleting models stays a human decision."]
    if truncated:
        notes.append(f"listing truncated at {s.ml_max_models} models — "
                     "narrow with --catalog/--schema for full coverage.")
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
    max_up = (
        args.max_uptime_hours if args.max_uptime_hours is not None else s.gpu_max_uptime_hours
    )
    findings = ml.classify_gpu_clusters(
        ml.fetch_clusters_with_node_types(w), ml.fetch_gpu_node_types(w), _now_ms(), max_up
    )
    emit(args, f"Interactive GPU clusters (uptime threshold {max_up}h)", findings,
         ["Report only — terminate via 'housekeeping stale-clusters --apply' "
          "or the owner."])
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
    findings = ml.find_vector_search_findings(
        ml.fetch_vector_search(w), _now_ms(), s.vector_search_grace_hours
    )
    emit(args, "Vector search endpoint audit", findings,
         ["Report only — endpoint deletion is irreversible and stays a human "
          "decision."])
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
    from dbx_platform import dashboards

    s = Settings.from_env()
    catalog = args.catalog or s.dashboard_catalog
    schema = args.schema or s.dashboard_schema
    tag_keys = (
        [t.strip() for t in args.team_tags.split(",") if t.strip()]
        if args.team_tags
        else s.required_tag_list()
    )
    w = get_client(args.profile)
    done = dashboards.run_setup(
        w, _warehouse_id(args, s), catalog, schema, tag_keys, args.workspace_name
    )
    for d in done:
        print(f"  created/updated: {d}")
    return 0


# --- report ------------------------------------------------------------------------

def cmd_report_ai_digest(args) -> int:
    from dbx_platform import digest

    s = Settings.from_env()
    w = get_client(args.profile)
    days = args.days if args.days is not None else s.lookback_days
    model = args.model or s.digest_model
    warehouse = _warehouse_id(args, s)
    findings, skipped = digest.collect_findings(w, s, warehouse, _now_ms(), days)
    counts = [
        {"check": k, "findings": len(v)} for k, v in sorted(findings.items())
    ]
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
                w, warehouse, s.dashboard_catalog, s.dashboard_schema,
                days, model, summary, findings,
            )
            print(f"  stored: {s.dashboard_catalog}.{s.dashboard_schema}."
                  "platform_digest/platform_findings")
        except (SystemTablesUnavailableError, RuntimeError, ValueError) as e:
            print(f"  note: not stored ({e}) — run 'dbx-platform dashboards setup' first.")
    return 0


# --- release ----------------------------------------------------------------------

def cmd_publish_wheel(args) -> int:
    from dbx_platform import release

    s = Settings.from_env()
    volume = args.volume or s.wheel_volume_path
    if not volume:
        print("error: pass --volume /Volumes/<catalog>/<schema>/<volume>/wheels "
              "or set DBX_PLATFORM_WHEEL_VOLUME_PATH", file=sys.stderr)
        return 2
    w = get_client(args.profile)
    dest = release.publish_wheel(w, volume, args.wheel)
    print(f"uploaded: {dest}")
    print(f"install from a notebook with:\n  %pip install {dest}")
    return 0


# --- parser ------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile",
                        help="Profile name in ~/.databrickscfg (default: unified auth)")
    common.add_argument("--warehouse-id", help="SQL warehouse for system-table queries")
    common.add_argument("--output", choices=["table", "json"], default="table")

    mutating = argparse.ArgumentParser(add_help=False)
    mutating.add_argument("--apply", action="store_true",
                          help="Execute the proposed actions (default: dry run)")
    mutating.add_argument("--yes", action="store_true",
                          help="Confirm --apply without prompting")

    p = argparse.ArgumentParser(prog="dbx-platform",
                                description="Databricks platform management toolkit")
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
    x = pc.add_parser("cluster-utilization", parents=[common],
                      help="Under-utilized clusters (CPU/memory vs size, by cost)")
    x.add_argument("--days", type=int, default=None)
    x.add_argument("--cpu-threshold", type=int, default=None, help="p95 CPU %% floor")
    x.add_argument("--mem-threshold", type=int, default=None, help="avg memory %% floor")
    x.set_defaults(func=cmd_cost_cluster_utilization)
    x = pc.add_parser("failed-run-waste", parents=[common],
                      help="$ burned on failed/timed-out job runs")
    x.add_argument("--days", type=int, default=None)
    x.add_argument("--limit", type=int, default=20)
    x.set_defaults(func=cmd_cost_failed_run_waste)
    x = pc.add_parser("warehouse-utilization", parents=[common],
                      help="SQL warehouses: idle spend or sustained queueing")
    x.add_argument("--days", type=int, default=None)
    x.set_defaults(func=cmd_cost_warehouse_utilization)

    # azure-cost
    pa = sub.add_parser("azure-cost",
                        help="Azure bill: ingest + report (Cost Management API)"
                        ).add_subparsers(dest="command")
    x = pa.add_parser("pull", parents=[common],
                      help="Pull the Azure bill into <catalog>.<schema>.azure_costs")
    x.add_argument("--days", type=int, default=None,
                   help="Window to (re-)pull, default 3 (use 365 to backfill)")
    x.add_argument("--subscription-id", default=None,
                   help="Azure subscription (default: DBX_PLATFORM_AZURE_SUBSCRIPTION_ID)")
    x.add_argument("--service-credential", default=None,
                   help="UC service credential name for keyless Azure auth")
    x.set_defaults(func=cmd_azure_cost_pull)
    x = pa.add_parser("report", parents=[common], help="Azure spend from the ingested bill")
    x.add_argument("--days", type=int, default=None)
    x.add_argument("--by", choices=["bucket", "service", "resource-group"],
                   default="bucket")
    x.set_defaults(func=cmd_azure_cost_report)
    x = pa.add_parser("spikes", parents=[common],
                      help="Per-bucket day-over-trailing-week spend spikes")
    x.add_argument("--days", type=int, default=None)
    x.set_defaults(func=cmd_azure_cost_spikes)

    # forecast
    pf = sub.add_parser("forecast",
                        help="ML cost forecasting: features, train, predict, monitor"
                        ).add_subparsers(dest="command")
    x = pf.add_parser("build-features", parents=[common],
                      help="Engineer lag/rolling/calendar features from azure_costs")
    x.add_argument("--days", type=int, default=None, help="History window (default 365)")
    x.set_defaults(func=cmd_forecast_build_features)
    x = pf.add_parser("train", parents=[common],
                      help="Backtest candidates, register + gate @champion "
                           "(needs dbx-platform[forecast])")
    x.add_argument("--folds", type=int, default=4)
    x.add_argument("--horizon", type=int, default=14)
    x.add_argument("--min-improvement", type=float, default=0.01,
                   help="Relative WAPE margin the challenger must win by")
    x.add_argument("--no-promote", action="store_true",
                   help="Preview: backtest + register + @challenger, but report "
                        "the gate's decision instead of moving @champion")
    x.add_argument("--model-name", default=None)
    x.add_argument("--experiment", default=None)
    x.set_defaults(func=cmd_forecast_train)
    x = pf.add_parser("predict", parents=[common],
                      help="Batch inference from @champion into cost_forecasts")
    x.add_argument("--horizon", type=int, default=None)
    x.add_argument("--model-name", default=None)
    x.set_defaults(func=cmd_forecast_predict)
    x = pf.add_parser("monitor", parents=[common],
                      help="PSI feature drift + matured-forecast accuracy; "
                           "exits 1 on retrain verdict")
    x.add_argument("--no-store", action="store_true",
                   help="Skip writing findings to platform_findings")
    x.set_defaults(func=cmd_forecast_monitor)
    x = pf.add_parser("status", parents=[common],
                      help="Champion/challenger registry status")
    x.add_argument("--model-name", default=None)
    x.set_defaults(func=cmd_forecast_status)

    # housekeeping
    ph = sub.add_parser("housekeeping", help="Cleanup reports").add_subparsers(dest="command")
    x = ph.add_parser("stale-clusters", parents=[common, mutating],
                      help="Stale / long-running clusters")
    x.add_argument("--stale-days", type=int, default=None)
    x.add_argument("--max-uptime-hours", type=int, default=None)
    x.set_defaults(func=cmd_stale_clusters)
    x = ph.add_parser("orphaned-jobs", parents=[common, mutating],
                      help="Jobs owned by missing principals")
    x.set_defaults(func=cmd_orphaned_jobs)
    x = ph.add_parser("jobs-on-all-purpose", parents=[common],
                      help="Jobs paying the all-purpose premium or pinning "
                           "large fixed clusters")
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
    x = pg.add_parser("policy-sync", parents=[common, mutating],
                      help="Diff/apply cluster policies from policies/*.json")
    x.add_argument("--policies-dir", default="policies")
    x.set_defaults(func=cmd_policy_sync)
    x = pg.add_parser("tag-compliance", parents=[common], help="Missing tags + untagged spend")
    x.add_argument("--required-tags", default=None, help="Comma-separated tag keys")
    x.add_argument("--days", type=int, default=None)
    x.set_defaults(func=cmd_tag_compliance)
    x = pg.add_parser("tag-recommendations", parents=[common],
                      help="Suggest fixes for missing tags (typo/near-match + inferred values)")
    x.add_argument("--required-tags", default=None, help="Comma-separated tag keys")
    x.set_defaults(func=cmd_tag_recommendations)

    # ml
    pm = sub.add_parser("ml", help="AI/ML workloads: serving, models, GPU, vector search"
                        ).add_subparsers(dest="command")
    x = pm.add_parser("endpoint-audit", parents=[common],
                      help="Serving endpoint hygiene: state, scale-to-zero, "
                           "inference tables, AI Gateway")
    x.add_argument("--stale-days", type=int, default=None)
    x.set_defaults(func=cmd_ml_endpoint_audit)
    x = pm.add_parser("model-hygiene", parents=[common],
                      help="UC registered models: stale, ownerless, unaliased, never served")
    x.add_argument("--catalog", default=None, help="Limit to one catalog")
    x.add_argument("--schema", default=None, help="Limit to one schema (needs --catalog)")
    x.add_argument("--stale-days", type=int, default=None)
    x.add_argument("--unaliased-days", type=int, default=None)
    x.set_defaults(func=cmd_ml_model_hygiene)
    x = pm.add_parser("serving-cost", parents=[common],
                      help="Serving/vector-search/AI spend and token usage")
    x.add_argument("--days", type=int, default=None)
    x.set_defaults(func=cmd_ml_serving_cost)
    x = pm.add_parser("gpu-audit", parents=[common],
                      help="Interactive GPU clusters + GPU spend share")
    x.add_argument("--max-uptime-hours", type=int, default=None)
    x.add_argument("--days", type=int, default=None)
    x.set_defaults(func=cmd_ml_gpu_audit)
    x = pm.add_parser("vector-search-audit", parents=[common],
                      help="Vector search endpoints: no indexes / unhealthy")
    x.set_defaults(func=cmd_ml_vector_search_audit)

    # dashboards
    pd = sub.add_parser("dashboards", help="AI/BI dashboards").add_subparsers(dest="command")
    x = pd.add_parser("render", parents=[common],
                      help="Render dashboards/templates -> dashboards/*.lvdash.json")
    x.add_argument("--catalog", default=None, help="Catalog for helper objects (default: main)")
    x.add_argument("--schema", default=None,
                   help="Schema for helper objects (default: dbx_platform)")
    x.add_argument("--dashboards-dir", default="dashboards")
    x.set_defaults(func=cmd_dashboards_render)
    x = pd.add_parser("setup", parents=[common],
                      help="Create the schema/functions/reference tables dashboards need")
    x.add_argument("--catalog", default=None)
    x.add_argument("--schema", default=None)
    x.add_argument("--team-tags", default=None,
                   help="Comma-separated tag keys used to derive team names "
                        "(default: required tags)")
    x.add_argument("--workspace-name", default=None,
                   help="Friendly name for the current workspace in cost dashboards")
    x.set_defaults(func=cmd_dashboards_setup)

    # report
    pp = sub.add_parser("report", help="Cross-area reports").add_subparsers(dest="command")
    x = pp.add_parser("ai-digest", parents=[common],
                      help="AI-summarized digest of all checks (ai_query on the "
                           "warehouse)")
    x.add_argument("--days", type=int, default=None)
    x.add_argument("--model", default=None,
                   help="Foundation-model serving endpoint (default: settings)")
    x.add_argument("--no-store", action="store_true",
                   help="Skip writing to the platform_digest/platform_findings tables")
    x.set_defaults(func=cmd_report_ai_digest)

    # release
    pr = sub.add_parser("release", help="Distribution helpers").add_subparsers(dest="command")
    x = pr.add_parser("publish-wheel", parents=[common],
                      help="Upload the built wheel to a UC Volume")
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

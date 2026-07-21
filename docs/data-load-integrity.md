# Data load integrity and cadence alignment

This platform keeps load results accurate by making scheduled writes rerunnable,
scoped, and observable. The strongest controls are in the Azure cost and forecast
pipelines because those tables feed downstream dashboards, alerts, and model
monitoring.

## Load accuracy controls

- Azure cost ingestion reloads a rolling three-day window so late Azure billing
  restatements are picked up by the next scheduled run.
- Cost rows are reconciled with scoped Delta `MERGE` statements keyed by workspace,
  environment, subscription, date, service/resource dimensions, and currency.
- Rows that disappear from the source are deleted only inside the exact requested
  workspace/environment/subscription/date window.
- Feature rows are merged by `(series, feature_date)` and forecast rows are merged
  by `(run_date, target_date, series)`, so retries update the same logical records
  instead of appending duplicates.

## Mid-load failure behavior

- Normal Azure cost loads use one atomic reconciliation statement for the requested
  window.
- Oversized cost payloads split into exact daily reconciliation statements; a rerun
  is safe because each day is merged by deterministic keys.
- Downstream tasks depend on upstream tasks in the Databricks job definitions, so
  spike detection waits for cost ingestion and forecast monitoring waits for both
  feature generation and prediction.
- Missing required tables or write grants fail loudly with migration guidance
  instead of silently producing partial downstream reports.

## Cadence and count alignment

- The Azure cost pull runs before the daily forecast job, giving forecast feature
  generation a refreshed fact table to read.
- Governed write commands receive Databricks job ID, run ID, trigger type, action
  ID, plan hash, and environment so the app can distinguish scheduled writes from
  approved manual runs.
- Store functions return row counts for the rows reconciled, and source-health
  records capture row counts, coverage windows, freshness, and source status where
  the pipeline maintains coverage metadata.
- Forecast accuracy is checked only after actuals mature; the monitor compares
  matured forecasts to actual cost rows and reports WAPE, P10/P90 interval coverage,
  and matured point counts by series.

## Known limitation

The repository does not currently implement one universal post-load assertion that
source counts must equal target counts for every pipeline and cadence. Alignment is
primarily enforced through scoped idempotent merges, deterministic logical keys,
ordered tasks, row-count/source-health metadata, and downstream monitors.

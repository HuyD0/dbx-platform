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

## Post-load count assertions

- After each coarse cost and resource/meter detail merge, the pull command reads
  daily target counts back from the exact workspace, environment, subscription,
  and date window.
- Source and target counts must match for every day, including source days with
  zero rows. Rows carrying a stale resource-group scope also fail validation.
- Normal payloads validate the whole atomic window by day. Oversized payloads use
  and validate independent daily atomic units.
- A mismatch or failed validation query raises an error, so dependent Databricks
  Job tasks do not run.

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

The count readback verifies the resulting state rather than trusting only the
number of rows submitted. If an oversized load stops after one daily unit, a rerun
reconciles the same deterministic keys and revalidates every daily unit.

## Forecast alignment

- Feature generation derives the exact expected `(series, feature_date)` keys
  from dense daily actuals and verifies the emitted keys before storage.
- Series with fewer than 28 prior days are reported as `insufficient-history`;
  missing, unexpected, or duplicate forecastable feature keys are errors.
- Prediction derives the forecastable series and requires exactly one row per
  series and horizon day before forecasts are stored. Short-history series are
  reported rather than treated as integrity failures.
- Monitoring classifies forecast points as matured, not yet mature, missing an
  actual, or invalid. Not-yet-mature points do not count as missing actuals.
- A matured forecast without an aligned actual produces a canonical
  `source-data-missing` finding and fails the monitoring command so the scheduled
  Job notification fires.

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

## Safety and remaining scope

These controls add no mutation path. Existing approval, job-context, and
least-privileged executor checks still run before governed writes, and the served
agent remains read-only.

The post-load count assertion currently covers Azure coarse and detail cost
ingestion rather than every pipeline in the repository. Other evidence pipelines
continue to rely on their scoped idempotent writes, normalized findings,
source-health metadata, ordered tasks, and subsystem-specific monitors.

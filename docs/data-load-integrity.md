# Data load integrity and cadence alignment

The Azure cost and forecasting pipelines fail closed when a write completes
without the expected logical rows. The checks are scoped to the same workspace,
environment, subscription, and date windows used by the governed write path.

## Azure cost reconciliation

- Coarse cost and resource/meter detail loads reconcile exact inclusive windows
  with deterministic Delta `MERGE` keys.
- After each merge, the pull command reads daily target counts back from the exact
  workspace, environment, subscription, and date window.
- Source and target counts must match for every day, including source days with
  zero rows. Rows carrying a stale resource-group scope also fail validation.
- Normal payloads validate the whole atomic window by day. Oversized payloads use
  and validate independent daily atomic units.
- A mismatch or failed validation query raises an error, so dependent Databricks
  Job tasks do not run.

Retries remain safe: the merge updates the same business keys and removes source
rows withdrawn inside the exact window. The count readback verifies the resulting
state rather than trusting only the number of rows submitted.

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

These controls add no mutation path. Existing approval, job-context, and
least-privileged executor checks still run before governed writes, and the served
agent remains read-only.

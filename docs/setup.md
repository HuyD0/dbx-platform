# Setup

## 1. Install

```bash
uv sync --extra dev
uv run dbx-platform --version
```

Install Databricks CLI v0.218 or newer.

## 2. Authenticate

```bash
databricks auth login \
  --host https://adb-<workspace-id>.<n>.azuredatabricks.net \
  --profile dbx-platform
databricks current-user me -p dbx-platform
```

Use one profile per workspace. Override bundle authentication with
`DATABRICKS_HOST` or `-p`; do not duplicate the host in source.

## 3. Create identity boundaries

Create/register:

- deployment identity or deployment group;
- app service principal (Databricks Apps creates/binds this);
- dedicated evidence-job executor (configured by the legacy
  `runtime_executor_service_principal_name` variable);
- distinct action executor;
- scheduled report identity/group;
- `dbx-platform-viewers`;
- `dbx-platform-operators`;
- `dbx-platform-approvers`.

Add authorized humans to approvers. Apply the exact workspace/API and Unity
Catalog grants in [service-principal.md](service-principal.md). Do not reuse
identities or grant executors blanket workspace admin.

## 4. Prepare the Unity Catalog schema

As metastore administrator:

```sql
CREATE SCHEMA IF NOT EXISTS main.dbx_platform;
ALTER SCHEMA main.dbx_platform OWNER TO `dbx-platform-deployers`;
```

The deployment identity owns only this application schema. Runtime/app/action
identities receive table-level grants and no DDL.

The migration also creates security-definer write-broker procedures for human
plans and decisions. Enable Catalog commits for the managed Delta action
tables before controlled action enablement so each multi-table decision runs
as one atomic block. Operators and approvers receive `EXECUTE` plus read
access—never direct `MODIFY` on the action ledger.

## 5. Enable evidence sources

Enable only the system schemas used by installed packs:

```bash
databricks metastores current -p dbx-platform
databricks system-schemas list <metastore-id> -p dbx-platform
databricks system-schemas enable <metastore-id> billing -p dbx-platform
databricks system-schemas enable <metastore-id> access -p dbx-platform
databricks system-schemas enable <metastore-id> lakeflow -p dbx-platform
databricks system-schemas enable <metastore-id> compute -p dbx-platform
databricks system-schemas enable <metastore-id> query -p dbx-platform
databricks system-schemas enable <metastore-id> serving -p dbx-platform
```

Grant the report identity `USE SCHEMA, SELECT` only on enabled schemas. Preview
AI Gateway sources are feature-detected; absence is an explicit data-health
state.

For Azure actual cost, give the managed identity behind the configured Unity
Catalog service credential Azure `Cost Management Reader` at the smallest
supported billing scope. See [secrets.md](secrets.md).

## 6. Validate and deploy proposal-only

```bash
export BUNDLE_VAR_runtime_executor_service_principal_name=<runtime-client-id>
export BUNDLE_VAR_action_executor_service_principal_name=<action-client-id>
export BUNDLE_VAR_workspace_display_name="Finance Analytics"
# actions_enabled defaults to false

uv run ruff check .
uv run pytest
databricks bundle validate -t dev -p dbx-platform
databricks bundle deploy -t dev -p dbx-platform
databricks bundle run schema_migrations -t dev -p dbx-platform
```

The bundle creates the dedicated `[dbx-platform] mission-control` 2X-Small
serverless warehouse with five-minute auto-stop. It does not use or manage a
shared Starter warehouse.

Every dev schedule deploys PAUSED and the warehouse starts only on demand; the
app deploys started (`resources/app.yml`). `schema_migrations` uses serverless
Spark and does not start the dedicated SQL warehouse. Production unpauses only
the five curated schedules documented in the runbook.

The migration job is the only dashboard/control-plane DDL bootstrap.
`dbx-platform dashboards setup` is deliberately disabled. Use:

```bash
dbx-platform dashboards health \
  --warehouse-id <dedicated-warehouse-id> \
  --profile dbx-platform
```

for a read-only dependency check.

## 7. Validate one evidence cycle

Before enabling actions, run/observe each scheduled pack once and verify:

- canonical findings include workspace/environment, evidence, freshness,
  impact, confidence, owner, lifecycle, and blast radius;
- Mission Control reports dependency/source health instead of raw SQL errors;
- LLM/Azure/Databricks cost bases remain separate and currencies are not
  silently combined;
- unavailable AI Gateway preview tables produce visible coverage gaps;
- the app service principal has no target mutation or UC `MODIFY`;
- paused packs remain visibly stale or unavailable instead of being reported
  as healthy.

Scheduled stateful writers attest the Jobs API’s exact periodic Job/run.
Run-now/manual execution must instead arrive through an approved `run-job`
action.

## 8. Test approval and executor safety

In proposal-only mode, validate negative cases:

- altered plan/payload/hash;
- spoofed forwarded identity;
- non-approver and removed approver;
- expired/replayed action;
- target/settings/version drift;
- missing/unwritable audit storage;
- direct legacy CLI flags;
- direct/mismatched forecast-training Job run.

Then exercise a low-risk valid action in a controlled dev scope and verify the
complete plan → approval → execution → verification event chain and exact-once
behavior.

## 9. Controlled enablement

Grant the action executor only the first allowlisted pack you intend to use.
Keep narrow permissions impossible to express (for example PAT revocation)
proposal-only.

Enable action submission through a reviewed bundle change:

```bash
export BUNDLE_VAR_actions_enabled=true
export BUNDLE_VAR_workspace_display_name="Finance Analytics"
databricks bundle validate -t prod
databricks bundle deploy -t prod
databricks bundle run schema_migrations -t prod
```

The app remains started, the warehouse starts on demand, and production keeps
only the five curated schedules active.

# Setup

## 1. Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # add ,azure inside the brackets for Key Vault access
dbx-platform --version
```

Install the [Databricks CLI](https://docs.databricks.com/dev-tools/cli/install) (v0.218+,
the new Go CLI — needed for `databricks bundle` commands and `databricks auth login`).

## 2. Authenticate (interactive OAuth)

```bash
databricks auth login --host https://adb-<workspace-id>.<n>.azuredatabricks.net --profile dbx-platform
databricks current-user me -p dbx-platform     # sanity check
```

This creates a named profile in `~/.databrickscfg`. Every `dbx-platform` command accepts
`--profile dbx-platform`; alternatively `export DATABRICKS_CONFIG_PROFILE=dbx-platform`.

**Multiple workspaces**: create one profile per workspace
(`databricks auth login --host ... --profile other-workspace`) and switch with
`--profile`. The bundle side is switched with the `DATABRICKS_HOST` env var or
`-p <profile>` — no code changes anywhere.

## 3. Pick a SQL warehouse

System-table queries and the dashboards run on a SQL warehouse (serverless preferred):

```bash
databricks warehouses list -p dbx-platform
export DBX_PLATFORM_WAREHOUSE_ID=<id>     # for dbx-platform commands
export BUNDLE_VAR_warehouse_id=<id>       # for bundle deploy
```

## 4. Enable system tables (one-time per metastore)

System schemas must be enabled by a metastore admin, and you need SELECT:

```bash
databricks metastores current -p dbx-platform     # note the metastore id
databricks system-schemas list <metastore-id> -p dbx-platform
databricks system-schemas enable <metastore-id> billing -p dbx-platform
databricks system-schemas enable <metastore-id> access -p dbx-platform
databricks system-schemas enable <metastore-id> lakeflow -p dbx-platform
databricks system-schemas enable <metastore-id> compute -p dbx-platform
databricks system-schemas enable <metastore-id> query -p dbx-platform
databricks system-schemas enable <metastore-id> serving -p dbx-platform  # only if using the ml commands
```

`system-schemas list` shows the current state — some schemas (e.g. `query`) may already
be `ENABLE_COMPLETED` or auto-enabled; enabling again is harmless.

Grants (run in a SQL editor as an admin):

```sql
GRANT USE SCHEMA, SELECT ON SCHEMA system.billing  TO `account users`;  -- or a tighter group
GRANT USE SCHEMA, SELECT ON SCHEMA system.access   TO `platform-admins`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.lakeflow TO `platform-admins`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.compute  TO `platform-admins`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.query    TO `platform-admins`;
```

These grants cover *you* running ad-hoc commands. The scheduled prod jobs run as the
CI service principal, which needs the same grants — see the system-table grants
section in [cloud-setup.md](cloud-setup.md).

Commands that need system tables fail with an actionable message
if the schemas aren't enabled; REST-only commands are unaffected.

## 5. Smoke test

```bash
# REST-only (no system tables needed):
dbx-platform housekeeping stale-clusters --profile dbx-platform

# System-tables path:
dbx-platform cost report --days 7 --profile dbx-platform
```

## 6. Dashboards: provisioning

The four dashboards query helper functions and reference tables (default location
`main.dbx_platform`). These are provisioned automatically by the `dashboards-setup`
job (`resources/dashboards_jobs.yml`), which runs daily and once per prod deploy
(deploy.yml runs `databricks bundle run dashboards_setup -t prod`), so you do not have
to provision them by hand before deploying.

Run setup yourself only to provision immediately — e.g. in `dev` (schedules are paused),
or to attach a friendly workspace name, or for a non-default catalog/schema (override
with `--catalog/--schema`, then re-render and update `dashboards_jobs.yml`, see README):

```bash
dbx-platform dashboards setup --profile dbx-platform \
  --workspace-name "my-workspace-friendly-name"
```

## 7. Deploy the bundle

```bash
databricks bundle validate -t dev -p dbx-platform
databricks bundle deploy   -t dev -p dbx-platform   # resources prefixed "[dev <you>]", schedules paused
databricks bundle run security_audit -t dev -p dbx-platform
```

When happy: `databricks bundle deploy -t prod -p dbx-platform` (active schedules, real names).

If serverless job compute is not enabled in your workspace/region, replace each job's
`environments` block with a `job_clusters` entry (small single-node cluster) and add
`libraries: [whl: ./dist/*.whl]` per task — see docs/runbook.md.

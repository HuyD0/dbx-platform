# dbx-platform

Toolkit for managing a Databricks platform: an admin CLI, scheduled jobs,
AI/BI dashboards, and cluster policies as code — deployed with
[Databricks Asset Bundles](https://docs.databricks.com/dev-tools/bundles/).

Every check is one code path exposed two ways: run it ad-hoc from your laptop,
or let the bundle-deployed job run it on a schedule (`python_wheel_task`
invoking the same `dbx-platform` entry point). Everything is **read-only by
default** — destructive actions require `--apply --yes`.

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

databricks auth login --host https://adb-<id>.<n>.azuredatabricks.net --profile dbx-platform
export DATABRICKS_CONFIG_PROFILE=dbx-platform

dbx-platform housekeeping stale-clusters        # first report, REST-only
```

Full setup (system tables, warehouse, dashboards): **[docs/setup.md](docs/setup.md)**.

Prefer not to use a local machine at all? **[docs/cloud-setup.md](docs/cloud-setup.md)**
wires up the browser-only loop: comment `@claude` on an issue → PR → CI → merge → deploy.

## What you get

### CLI / scheduled jobs

| Area | Command | Scheduled job | What it does |
|---|---|---|---|
| Cost | `cost report` | `cost-usage-report` (daily) | DBU + list-price cost by SKU/workspace (`system.billing`) |
| Cost | `cost top-jobs` | 〃 | Most expensive jobs, joined to `system.lakeflow.jobs` |
| Housekeeping | `housekeeping stale-clusters` | `housekeeping-report` (daily) | Long-terminated clusters, long-running/no-autoterm clusters. `--apply` terminates/deletes |
| Housekeeping | `housekeeping orphaned-jobs` | 〃 | Jobs owned by deleted/inactive principals. `--apply` pauses (never deletes) |
| Security | `security token-audit` | `security-audit` (weekly) | No-expiry / over-age / expiring-soon PATs. `--apply` revokes over-age |
| Security | `security inactive-users` | 〃 | Active users with zero audited activity (`system.access.audit`) |
| Governance | `governance policy-sync` | `governance-check` (weekly, drift report) | `policies/*.json` vs workspace. `--apply` creates/updates; never deletes unmanaged |
| Governance | `governance tag-compliance` | 〃 | Resources missing required tags + % of spend untagged |
| Dashboards | `dashboards setup` / `render` | — | Provision dashboard helper objects / re-render templates |
| Release | `release publish-wheel` | — | Upload the wheel to a UC Volume for notebook reuse |

Operational details, thresholds, and how to act on findings: **[docs/runbook.md](docs/runbook.md)**.

### Dashboards

Four AI/BI dashboards deployed by the bundle (`resources/dashboards.yml`):
Unified Cost Analysis, Job Operations & Cost Management, DBSQL Cost & Query
Performance, Data Lineage & Catalog Utilization.

Adapted from the community
[databricks-dashboard-suite](https://github.com/mohanab89/databricks-dashboard-suite)
by mohanab89 (system-table dashboards, provided as-is; the upstream repo has
**no license file** — JSON is vendored under `dashboards/templates/` with this
attribution). One-time provisioning of their helper functions/tables:
`dbx-platform dashboards setup` (see [docs/setup.md](docs/setup.md) §6).

### Secrets helper

```python
from dbx_platform.secrets import get_secret
get_secret("dbx://scope/key")      # Databricks secret scope — works locally AND in jobs
get_secret("akv://vault/name")     # Azure Key Vault via UC service credential / managed identity
```

Setup and the decision table Databricks-vs-Azure constructs: **[docs/secrets.md](docs/secrets.md)**.

## Swapping workspaces

The workspace URL exists in **exactly one place**: the `workspace_host`
variable default in [databricks.yml](databricks.yml) (convention: `adb-`
appears in no other file). To target another workspace:

```bash
# bundle:
databricks bundle deploy --var workspace_host=https://adb-<other>.azuredatabricks.net
# CLI: one profile per workspace
databricks auth login --host https://adb-<other>... --profile other
dbx-platform cost report --profile other
```

Inside a Databricks job, `WorkspaceClient()` uses the runtime's own
credentials — nothing to configure.

## Repo layout

```
databricks.yml          bundle: variables (workspace_host, warehouse_id), dev/prod targets
resources/              job + dashboard definitions (schedules/thresholds live here, in git)
dashboards/             rendered .lvdash.json (deployed) + templates/ (upstream, pristine)
policies/               cluster policies as code — git is the source of truth
src/dbx_platform/       the package: cli, client, config, area modules, queries/*.sql
tests/                  offline unit tests for all decision logic
docs/                   setup, runbook, secrets, service-principal (CI) guides
.github/workflows/      ci.yml (lint/test/build always; validate when secrets set), deploy.yml
```

## Development

```bash
ruff check . && pytest            # what CI runs; tests are offline (no workspace needed)
databricks bundle validate -t dev
databricks bundle deploy   -t dev # "[dev <you>]"-prefixed resources, schedules paused
```

CI deploys to prod on merge to `main`, authenticating with keyless GitHub OIDC (no
service-principal secret) — **[docs/cloud-setup.md](docs/cloud-setup.md)**.
[docs/service-principal.md](docs/service-principal.md) covers the older
client-secret setup and the non-admin variant.

## Future work

- Persist job findings to a UC Delta table and chart trends in the dashboards
- Budget alerts (`system.billing.usage` vs monthly targets)
- SCIM-integrated offboarding automation (deactivate + reassign in one step)

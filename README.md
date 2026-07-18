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
| Governance | `governance tag-recommendations` | 〃 | Suggests fixes for missing tags: typo/format near-match key renames + inferred values (report-only) |
| Cost | `cost cluster-utilization` | `cost-usage-report` (daily) | Under-utilized clusters (CPU/memory vs size, `system.compute.node_timeline`), ranked by cost |
| Cost | `cost failed-run-waste` | 〃 | $ burned on failed/timed-out job runs |
| Cost | `cost warehouse-utilization` | 〃 | SQL warehouses: idle spend or sustained queueing |
| Housekeeping | `housekeeping jobs-on-all-purpose` | `housekeeping-report` (daily) | Jobs paying the all-purpose premium / pinning large fixed clusters |
| ML | `ml endpoint-audit` | `ml-serving-report` (daily) | Serving endpoints: failed state, scale-to-zero, inference tables, AI Gateway, no traffic |
| ML | `ml serving-cost` | 〃 | AI/ML spend by product/SKU/endpoint + token usage (`system.serving`) |
| ML | `ml model-hygiene` | `ml-hygiene-report` (weekly) | UC models: stale, ownerless, unaliased, never served |
| ML | `ml gpu-audit` | 〃 | Interactive GPU clusters + GPU spend share |
| ML | `ml vector-search-audit` | 〃 | Vector search endpoints with no indexes / unhealthy |
| Report | `report ai-digest` | `platform-digest` (weekly) | AI-summarized digest of all checks via `ai_query()`, stored to UC tables |
| Dashboards | `dashboards setup` / `render` | `dashboards-setup` (daily) | Provision dashboard helper objects / re-render templates |
| Release | `release publish-wheel` | — | Upload the wheel to a UC Volume for notebook reuse |

All ML and right-sizing checks are report-only by design (endpoint config
changes redeploy the endpoint; model/endpoint deletion is irreversible).

Operational details, thresholds, and how to act on findings: **[docs/runbook.md](docs/runbook.md)**.

### Dashboards

Four AI/BI dashboards deployed by the bundle (`resources/dashboards.yml`):
Unified Cost Analysis, Job Operations & Cost Management, DBSQL Cost & Query
Performance, Data Lineage & Catalog Utilization.

Adapted from the community
[databricks-dashboard-suite](https://github.com/mohanab89/databricks-dashboard-suite)
by mohanab89 (system-table dashboards, provided as-is; the upstream repo has
**no license file** — JSON is vendored under `dashboards/templates/` with this
attribution). Their helper functions/tables are provisioned automatically by the
`dashboards-setup` job (`resources/dashboards_jobs.yml`) — daily and once per prod
deploy — so a fresh deploy works without a manual step. Run `dbx-platform dashboards
setup` yourself only to provision immediately or for a custom catalog/schema (see
[docs/setup.md](docs/setup.md) §6).

### Platform Console app

A Databricks App (`apps/platform-console`, FastAPI + React) deployed by the
bundle (`resources/app.yml`) — the third surface of the same code path. A
dark-mode dashboard with an overview of stored findings, per-area pages that
run every check live (cost, housekeeping, security, governance, AI/ML), AI
digests, job kick-off, and a chat page backed by the served platform agent.
Report-only by default; four conservative remediation actions (stale-cluster
cleanup, orphaned-job pause, over-age token revoke, policy sync) exist behind
an off-by-default env gate plus a dry-run plan and typed confirmation — see
docs/runbook.md. Local dev: `python main.py` for the API, `npm run dev` in
`frontend/` for the UI.

### AI layer

- **`report ai-digest`** summarizes every area's findings with one
  `ai_query()` call to a Databricks-hosted foundation model on your SQL
  warehouse — no extra credentials. Digest + findings persist to
  `platform_digest` / `platform_findings` (created by `dashboards setup`).
- **Triage loop**: `.github/workflows/platform-triage.yml` runs the checks
  weekly and upserts a rolling GitHub issue mentioning `@claude`, which
  proposes fixes as pull requests (policy drift → `policies/*.json`, job
  right-sizing → job specs). The agent only proposes git changes; `--apply`
  stays human-invoked.
- **Served agent** (`agents/platform_agent`, optional `[agent]` extra): a
  read-only LangGraph agent over the same checks, deployed to model serving
  via the Mosaic AI Agent Framework (`python agents/platform_agent/deploy_agent.py`).
  Its tool set wraps no mutating function, so it can diagnose and recommend
  but never change the workspace. The Platform Console's Chat page talks to
  it; its `propose_*` tools emit dry-run proposals the console renders as
  confirm-gated action cards — a human always performs the apply.

### Secrets helper

```python
from dbx_platform.secrets import get_secret
get_secret("dbx://scope/key")      # Databricks secret scope — works locally AND in jobs
get_secret("akv://vault/name")     # Azure Key Vault via UC service credential / managed identity
```

Setup and the decision table Databricks-vs-Azure constructs: **[docs/secrets.md](docs/secrets.md)**.

## Swapping workspaces

The workspace URL exists in **exactly one place**: `workspace.host` in
[databricks.yml](databricks.yml) (convention: `adb-` appears in no other file).
It cannot be a bundle variable — the CLI rejects interpolation for fields that
configure authentication — so override it at runtime instead:

```bash
# bundle: env var or profile (the CLI's documented overrides)
DATABRICKS_HOST=https://adb-<other>.azuredatabricks.net databricks bundle deploy
databricks bundle deploy -p other
# CLI: one profile per workspace
databricks auth login --host https://adb-<other>... --profile other
dbx-platform cost report --profile other
```

Inside a Databricks job, `WorkspaceClient()` uses the runtime's own
credentials — nothing to configure.

## Repo layout

```
databricks.yml          bundle: workspace.host, variables (warehouse_id), dev/prod targets
resources/              job + dashboard definitions (schedules/thresholds live here, in git)
dashboards/             rendered .lvdash.json (deployed) + templates/ (upstream, pristine)
policies/               cluster policies as code — git is the source of truth
src/dbx_platform/       the package: cli, client, config, area modules, queries/*.sql
tests/                  offline unit tests for all decision logic
docs/                   setup, runbook, secrets, service-principal (CI) guides
.github/workflows/      ci.yml (lint/test/build on PRs; bundle validate on push), deploy.yml, claude.yml
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

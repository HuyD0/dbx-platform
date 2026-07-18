# dbx-platform AI Mission Control

dbx-platform is a single-workspace control tower for operating Azure
Databricks across cost, security, risk, and performance.

It continuously observes the platform, normalizes evidence into findings, and
uses AI to correlate symptoms and draft proposals. AI is never an executor.
Every Databricks/Azure resource, credential, policy, budget, schedule, model,
training, or runtime mutation follows:

`Observe → correlate → propose → human approve → execute → verify → measure`

The Platform Console is a FastAPI + React Databricks App. Scheduled jobs gather
evidence and append findings/cost telemetry. Dedicated, unscheduled executor
jobs are the only mutation boundary.

## Safety model

- The app service principal is read-only. It may submit an approved action ID
  to an executor; it cannot submit an execution payload or mutate targets.
- An authorized member of `dbx-platform-approvers` approves one immutable,
  SHA-256-hashed plan. Self-approval is allowed for group members.
- Plans expire after 15 minutes, are single-use, name exact targets, and carry
  before state, preconditions, rollback, verification, impact, and blast
  radius.
- Executors reload the durable Unity Catalog record, verify current approver
  membership, hash, TTL, allowlist, workspace/environment, target versions, and
  current state before writing.
- Payload drift, target drift, replay, missing identity, or unavailable audit
  storage fails closed.
- Legacy `--apply`/`--yes` paths cannot authorize changes. Resource deletion is
  unsupported in v1.
- Autonomous schedules may read sources and append internal findings, usage,
  cost, and audit telemetry. Training, model promotion, budget/config changes,
  manual stateful job runs, remediation, and runtime control require approval.

See [docs/runbook.md](docs/runbook.md) for the operator flow and
[docs/service-principal.md](docs/service-principal.md) for the exact identity
and grant matrix.

## Product surfaces

The console navigation is organized around decisions rather than source
systems:

1. **Mission Control** — scope, source health, cost/security/risk/performance
   outcomes, pending approvals, what changed, and the top decisions.
2. **Action Center** — recommendations, awaiting approval, activity, failures,
   and rollback outcomes.
3. **Cost & Value** — Databricks, Azure, LLM/AI, budgets, forecast, and unit
   economics.
4. **Security & Risk** — identity, credentials, grants, ownership, policies,
   egress, and audit anomalies.
5. **Performance** — job/query regressions, queueing, retry waste,
   utilization, serving latency/errors, and SLO risk.
6. **Resources & Runtime** — exact owned inventory, dependencies, Hibernate,
   and Wake.
7. **Automations** — report schedules, monitors, and playbooks.
8. **Assistant**, with Settings and Audit available globally.

All operational observations use the canonical `platform_findings` schema:
pillar, severity, likelihood, financial/SLO impact, confidence, owner,
affected resources, evidence, freshness, first/last seen, blast radius, and
lifecycle state. Ranking is deterministic: critical security/SLO impact,
estimated financial impact, then age.

The contextual assistant receives the current page/filter/resource context. It
can explain evidence and draft structured proposals, but cannot call an
executor. Responses must cite a source table/query, timestamp, or resource.

## LLM Cost & Value

The provider-aware ledger separates, rather than silently blending:

- `Azure actual` billing;
- `Databricks list` cost from billing usage/list prices;
- `provider estimate` from AI Gateway preview sources.

It combines those sources with serving/AI Gateway request telemetry for
requests, input/output/cached/reasoning tokens when present, latency, errors,
retries, cost/request, cost/1M tokens, and cost per successful task. Missing
preview sources, uncovered spend, incomplete token coverage, and currency
boundaries are visible data-health states.

The rollup keeps 90 days of hourly detail and 400 days of daily aggregates.
Budgets default to 80% warning and 100% critical alerts; changing a budget is
an approved action and an alert never changes resources automatically.

## Safe Hibernate and Wake

The bundle creates a dedicated 2X-Small serverless SQL warehouse with a
five-minute auto-stop. It never manages or reuses the shared Starter warehouse.

The exact v1 Hibernate inventory is:

- the Platform Console app;
- thirteen bundle-declared schedules;
- the dedicated Mission Control warehouse.

The unscheduled `power-controller` and `action-executor` jobs, manual forecast
training, dashboards, Unity Catalog data, models, shared compute, storage,
networking, and unrelated projects are protected/out of scope.

Hibernate records exact prior state, pauses only schedules that were enabled,
waits up to 15 minutes for owned runs/queries to drain, stops the warehouse,
then stops the app. Wake starts the warehouse, starts and health-checks the
app, and restores only the schedules enabled before Hibernate. Partial failure
restores captured state where possible and records the result.

All bundle schedules deploy PAUSED and the warehouse deploys stopped; the app
deploys started, so a prod deploy starts it directly. Deployments run schema
migrations on serverless Spark, then produce a proposal-only runtime
reconciliation. Deploying while `SLEEPING` still restarts the app but leaves
the warehouse and schedules asleep.

## Commands and jobs

Read-only/advisory CLI examples:

```bash
dbx-platform cost report --days 30
dbx-platform security token-audit
dbx-platform governance policy-sync
dbx-platform dashboards health
```

The legacy mutator flags remain parseable only to fail with a migration
message:

```bash
dbx-platform housekeeping stale-clusters --apply --yes  # exits 2; no mutation
```

Dashboard DDL is applied only by the unscheduled `schema_migrations` deployment
job. `dashboards setup`, direct UC Volume wheel publication, and direct agent
registration/deployment are disabled. Forecast training is an unscheduled,
protected manual job whose task verifies the exact approved action, plan hash,
workspace/environment, Job ID, and executor-recorded run ID before MLflow
logging, registration, or alias promotion.

LLM/Azure rollups, forecast feature/inference/monitoring, and the AI digest are
scheduled governed writers. A direct/manual invocation is rejected unless an
approved `run-job` action launched that exact Job run.

The scheduled evidence jobs are:

| Pack | Evidence |
|---|---|
| Cost | DBU/list cost, expensive jobs, utilization, failed-run waste, SQL queueing |
| Azure | actual cost ingestion, detailed AI allocation, anomaly evidence |
| LLM/AI | provider-aware costs, tokens, request quality/latency, coverage |
| Security | PAT/user activity and audit evidence |
| Governance | policy/tag drift and tag recommendations |
| ML | customer-managed endpoint, model, GPU, and vector-search hygiene |
| AI catalog | unified model + access inventory (UC, serving, Azure AI); key-auth and broad-grant evidence |
| AI monitoring | per-app serving usage and error rates; spike, tracking-gap, and idle evidence |
| Performance | job/query/serving regressions and cost-versus-SLO evidence |
| Digest | canonical findings and AI summary |
| Dashboard health | read-only helper-object availability |

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

databricks auth login \
  --host https://adb-<workspace-id>.<n>.azuredatabricks.net \
  --profile dbx-platform

export BUNDLE_VAR_runtime_executor_service_principal_name=<runtime-executor-client-id>
export BUNDLE_VAR_action_executor_service_principal_name=<action-executor-client-id>

ruff check .
pytest
databricks bundle validate -t dev -p dbx-platform
databricks bundle deploy -t dev -p dbx-platform
databricks bundle run schema_migrations -t dev -p dbx-platform
```

Leave `actions_enabled=false` through proposal-only validation. Before
controlled enablement, create the approver group, apply the least-privilege
grants, and validate one complete scheduled reporting cycle. Full setup:
[docs/setup.md](docs/setup.md).

## Repository layout

```text
apps/platform-console/     React/FastAPI Mission Control
agents/platform_agent/     read-only contextual assistant
src/dbx_platform/          evidence packs, ledger, migrations, executors
resources/                 Asset Bundle jobs, app, warehouse, dashboards
dashboards/                AI/BI templates and rendered definitions
policies/                  reviewable policy source
tests/                     offline safety, decision, API, and runtime tests
docs/                      setup, grants, runbook, secrets, cloud CI
```

## Development

```bash
ruff check .
pytest
python -m build --wheel
databricks bundle validate -t dev
```

The frontend production build runs from
`apps/platform-console/frontend` with `npm ci && npm run build`.
CI authenticates to Databricks only on protected workspace-touching jobs; PR
tests remain credential-free and offline.

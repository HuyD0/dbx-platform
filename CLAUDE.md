# dbx-platform

AI Mission Control for one Azure Databricks workspace: a React/FastAPI App,
scheduled evidence jobs, durable human-approved actions, dedicated executors,
AI/BI dashboards, and policy source deployed with Databricks Asset Bundles.

## Core design invariant

Evidence checks share package logic across CLI, scheduled Jobs, API summaries,
and the assistant. Scheduled stateful/append-only commands attest their exact
Job/run/trigger; a manual run must instead match a durable `run-job` approval.
When adding a check, update its package logic, schedule (if any), normalized
finding, source-health metadata, and tests together.

## Safety model

AI, the app service principal, and legacy CLI commands never execute target
mutations. `--apply`/`--yes` are compatibility flags that always fail.

Every managed-resource/configuration mutation requires one exact immutable,
15-minute, single-use plan; current `dbx-platform-approvers` membership; a
typed confirmation for medium/high risk; revalidation; a dedicated
least-privileged executor; and append-only execution/verification events.
Missing identity/audit storage, altered payload/hash, drift, expiry, or replay
fails closed. Resource deletion is unsupported.

Schedules may read and append internal findings/cost/audit telemetry. Training,
model promotion, budgets/configuration, manual stateful Jobs, remediation, and
runtime state require approval. The served agent remains read-only and can only
cite evidence/draft proposals.

## Layout

| Path | Contents |
|---|---|
| `src/dbx_platform/` | CLI + one module per area (`cost`, `azure_cost`, `forecast_*`, `security`, `governance`, `housekeeping`, `dashboards`) |
| `src/dbx_platform/queries/` | SQL against Databricks system tables |
| `resources/*.yml` | Bundle job definitions, included by `databricks.yml` |
| `policies/*.json` | Cluster policies as code, reconciled by `governance policy-sync` |
| `dashboards/templates/` | Dashboard sources; `dashboards/*.lvdash.json` are rendered outputs |

## Packaging gotcha

Scheduled jobs have **no repo checkout** — they only have the wheel. So any data file
a job needs must ship inside it. `queries/` and `policies/` are force-included via
`[tool.hatch.build.targets.wheel.force-include]` in `pyproject.toml`, and CI asserts
they are present in the built wheel. If you add a new data directory a job reads at
runtime, add it there and extend that CI check.

## Workspace configuration

The workspace URL lives in **exactly one place**: `workspace.host` in `databricks.yml`.
Do not hardcode a workspace URL anywhere else, and do **not** try to make it a
`${var.…}` reference — the CLI rejects interpolation for fields that configure
authentication, which silently breaks every bundle command. Override it at runtime with
the `DATABRICKS_HOST` env var or a profile.

The bundle owns a dedicated 2X-Small serverless warehouse; do not reintroduce a
shared `warehouse_id` bundle variable.

## Targets

- `dev` (default) — `mode: development`, resources prefixed `[dev <user>]`, schedules
  paused, deployed under your user folder. Safe to iterate.
- `prod` — real names, all schedules PAUSED and app/warehouse stopped until an
  approved reconciliation. Deployed by CI on push to `main`.

## Commands

```bash
pip install -e ".[dev]"
ruff check .                       # lint (line-length 100)
pytest                             # tests
databricks bundle validate -t dev  # validate bundle
```

Tests mock the Databricks SDK — they do not require workspace credentials, and should
stay that way so CI runs without secrets.

## CI/CD

- `ci.yml` — `lint-test-build` (credential-free, runs on PRs); `bundle-validate`
  (push only, needs the workspace).
- `deploy.yml` — `bundle deploy -t prod` on push to `main` or manual dispatch.
- `claude.yml` — `@claude` mentions on issues/PRs.

**Auth is keyless OIDC** — no Databricks client secret exists. `azure/login@v2` exchanges
a GitHub OIDC token for an Azure token, and the Databricks CLI consumes it via
`DATABRICKS_AUTH_TYPE=azure-cli`. Two rules follow, and breaking either is silent:

- Any workspace-touching job **must** declare `environment: production` and
  `permissions: id-token: write`. The federated credential's subject ends in
  `:environment:production` (using this repo's immutable OIDC prefix, `repo:<owner>@<id>/
  <repo>@<id>` — not the plain `repo:owner/repo`); without the environment the token
  carries a different subject and Azure refuses the exchange.
- Never put `azure/login` on a `pull_request` trigger — that would expose a
  workspace credential to untrusted PR-branch code.

Never commit a credential. Full setup and the provisioned IDs: `docs/cloud-setup.md`.

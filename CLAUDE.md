# dbx-platform

Databricks platform-management toolkit: admin CLI, scheduled jobs, AI/BI dashboards,
and cluster policies as code, deployed with Databricks Asset Bundles.

## Core design invariant

Every check is **one code path exposed two ways**: an ad-hoc CLI command, and a
bundle-deployed job that runs the same code on a schedule via `python_wheel_task`
against the `dbx-platform` entry point. When you add a check, wire up both — a CLI
subcommand in `src/dbx_platform/cli.py` and a task in the matching `resources/*.yml`.
Never let the scheduled path drift into a separate implementation.

## Safety model

Everything is **read-only by default**. Mutating commands are dry-run unless given
`--apply`, and `--apply` additionally requires `--yes` (or `DBX_PLATFORM_CONFIRM=true`
for non-interactive contexts). Preserve this for any new mutating command. Existing
`--apply` actions are deliberately conservative: orphaned jobs are *paused, never
deleted*, and policy sync *never deletes* unmanaged policies.

## Layout

| Path | Contents |
|---|---|
| `src/dbx_platform/` | CLI + one module per area (`cost`, `security`, `governance`, `housekeeping`, `dashboards`) |
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

`warehouse_id` has no default and must be supplied (`BUNDLE_VAR_warehouse_id`); the
dashboards and system-table tasks need it.

## Targets

- `dev` (default) — `mode: development`, resources prefixed `[dev <user>]`, schedules
  paused, deployed under your user folder. Safe to iterate.
- `prod` — real names, active schedules, shared root path. Deployed by CI on push to `main`.

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

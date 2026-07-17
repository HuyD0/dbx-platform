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

The workspace URL lives in **exactly one place**: the `workspace_host` variable default
in `databricks.yml`. Do not hardcode a workspace URL anywhere else. Override with
`--var workspace_host=...` or `BUNDLE_VAR_workspace_host`.

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

- `ci.yml` — lint, test, build wheel, verify packaged data, on PR and push to `main`.
  The `bundle-validate` job self-skips when `DATABRICKS_HOST` is unset.
- `deploy.yml` — `bundle deploy -t prod` on push to `main`. Self-skips without secrets.
- `claude.yml` — `@claude` mentions on issues/PRs.

Workspace credentials come from repo secrets (`DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`,
`DATABRICKS_CLIENT_SECRET`, `DATABRICKS_WAREHOUSE_ID`) — see `docs/service-principal.md`.
Never commit a credential; the deploy path authenticates as a service principal via
OAuth M2M.

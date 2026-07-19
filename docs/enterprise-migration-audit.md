# Enterprise workspace migration audit

This audit highlights the repo settings that most often make an enterprise
Databricks workspace migration harder. Treat it as a pre-flight checklist before
pointing CI or a local profile at a new workspace.

## Keep or preserve

- Keep exactly one checked-in workspace URL: `workspace.host` in
  `databricks.yml`. For a different enterprise workspace, prefer the runtime
  override (`DATABRICKS_HOST`) or a Databricks CLI profile instead of copying the
  host into jobs, scripts, docs, or app code.
- Keep the dedicated 2X-Small serverless SQL warehouse. Reusing an existing
  shared warehouse makes ownership, Hibernate/Wake, and blast-radius checks
  ambiguous.
- Keep the production deployment allowlist in `.github/workflows/deploy.yml`.
  It prevents bundle deploys from replacing Lakeview dashboards or crossing an
  unreviewed destructive plan.
- Keep generated dashboard outputs (`dashboards/*.lvdash.json`) synchronized
  from `dashboards/templates/`; do not hand-edit rendered definitions during a
  workspace move.

## Remove or reconfigure for a new enterprise workspace

- Replace repository/environment secrets and variables rather than editing
  source:
  - `DATABRICKS_HOST`
  - `AZURE_CLIENT_ID`
  - `AZURE_TENANT_ID`
  - `AZURE_SUBSCRIPTION_ID`
  - `DBX_PLATFORM_RUNTIME_EXECUTOR_SP`
  - `DBX_PLATFORM_ACTION_EXECUTOR_SP`
  - `DBX_PLATFORM_AZURE_SERVICE_CREDENTIAL`
  - `DBX_PLATFORM_ACTIONS_ENABLED`
- Override bundle variables for enterprise names where needed:
  - `control_plane_catalog`
  - `control_plane_schema`
  - `notification_email`
  - `approver_group`
  - `operator_group`
  - `platform_console_name`
- Remove any temporary workspace-admin membership after applying the scoped
  grants in `docs/service-principal.md`. Workspace admin should not be the final
  steady-state permission model for deployment, app, runtime, or action
  executor identities.
- Keep `actions_enabled=false` until proposal-only validation, one full evidence
  cycle, approval negative tests, and a low-risk executor test all pass in the
  target workspace.

## Migration blockers to check before deploy

- The GitHub OIDC federated credential must be scoped to the protected
  `production` environment subject used by this repo. Workspace-touching
  workflows must keep `environment: production` and `permissions: id-token:
  write`.
- `azure/login` must not be added to pull-request workflows; PR code must stay
  credential-free.
- The deployment identity must be registered in the target Databricks workspace
  and able to deploy the bundle, run `schema_migrations`, and manage only the
  bundle-owned resources.
- The runtime executor and action executor must be distinct service principals
  unless a reviewed exception explicitly sets `DBX_PLATFORM_ALLOW_SHARED_EXECUTOR_SP`.
- Unity Catalog `main.dbx_platform` in examples is only a default. Enterprise
  workspaces should either create that schema or set `control_plane_catalog` and
  `control_plane_schema` consistently before migrations.
- System schemas (`billing`, `access`, `lakeflow`, `compute`, `query`, and
  `serving`) must be enabled only as needed, and the report identity must have
  `USE SCHEMA` and `SELECT` grants on each enabled source.
- Azure Cost and AI catalog inventory require Azure RBAC for the managed
  identity behind the configured Unity Catalog service credential; missing RBAC
  should surface as source-health gaps, not as hidden success.

## Recommended dry run sequence

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest
DATABRICKS_HOST=https://adb-<workspace-id>.<n>.azuredatabricks.net \
  databricks bundle validate -t dev -p <profile>
```

For production, deploy with `actions_enabled=false`, run `schema_migrations`,
then run the power-controller reconciliation in proposal-only mode. Approve Wake
only after the app health endpoint, source-health states, and one scheduled
evidence cycle are clean.

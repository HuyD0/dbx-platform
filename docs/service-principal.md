# CI/CD with a service principal (client-secret variant)

> **Not the current setup.** CI/CD now authenticates with **keyless GitHub OIDC** — no
> client secret exists — see **[cloud-setup.md](cloud-setup.md)**. The workflows no
> longer read `DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET`, so following the
> steps below will not activate them. Kept for the non-admin SP guidance in §2 and as
> a reference for the client-secret approach.

Local use runs under your interactive OAuth login. CI cannot log in interactively,
so the GitHub Actions workflows stay in "skip" mode until you configure a service
principal. Recommended: Databricks-native OAuth M2M (simplest); an Entra ID
service principal works too (variant below).

## 1. Create the service principal

Account console (https://accounts.azuredatabricks.net) → **User management** →
**Service principals** → **Add service principal** (e.g. `dbx-platform-ci`).

Or CLI:

```bash
databricks account service-principals create --display-name dbx-platform-ci
```

## 2. Grant workspace access + admin

Workspace admin console → **Identity and access** → **Service principals** →
add `dbx-platform-ci` → grant **Admin** role.

Admin is required because the security job calls the token-management and SCIM
APIs, and the bundle deploys jobs/dashboards. If you'd rather not grant admin,
strip the security job from the deployment and a non-admin SP with the
"workspace access" entitlement can deploy the rest.

## 3. Create an OAuth secret

Account console → the service principal → **Secrets** → **Generate secret**.
Note the client ID (application ID) and secret.

## 4. Configure GitHub repository secrets

Repo → Settings → Secrets and variables → Actions:

| Secret | Value |
|---|---|
| `DATABRICKS_HOST` | `https://adb-<workspace-id>.<n>.azuredatabricks.net` |
| `DATABRICKS_CLIENT_ID` | service principal application ID |
| `DATABRICKS_CLIENT_SECRET` | the OAuth secret |
| `DATABRICKS_WAREHOUSE_ID` | SQL warehouse for dashboards/system-table tasks |

The CI `bundle validate` step and the deploy workflow activate automatically once
`DATABRICKS_HOST` exists.

## 5. Run production jobs as the service principal (recommended)

In `databricks.yml`, add under the `prod` target so scheduled jobs stop running
as a human:

```yaml
  prod:
    mode: production
    run_as:
      service_principal_name: <application-id>
```

## Entra ID variant

If you standardize on Entra ID service principals instead, create an Entra app
registration, add it to the workspace, and replace the two client secrets with:

- `ARM_TENANT_ID`, `ARM_CLIENT_ID`, `ARM_CLIENT_SECRET`

and export those names in the workflows instead of `DATABRICKS_CLIENT_ID/SECRET`.
The Databricks CLI/SDK unified auth picks either pair up automatically.

## 6. Grant data access for the SP

For system-table tasks running as the SP:

```sql
GRANT USE SCHEMA, SELECT ON SCHEMA system.billing  TO `<application-id>`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.access   TO `<application-id>`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.lakeflow TO `<application-id>`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.compute  TO `<application-id>`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.query    TO `<application-id>`;
-- dashboards' helper schema (CREATE SCHEMA lets the dashboards-setup job create
-- dbx_dev.dbx_platform on first run; ALL PRIVILEGES covers the objects inside).
-- dbx_dev is the pre-existing catalog this workspace uses — its metastore has no
-- `main` catalog (Default Storage):
GRANT USE CATALOG ON CATALOG dbx_dev TO `<application-id>`;
GRANT CREATE SCHEMA ON CATALOG dbx_dev TO `<application-id>`;
GRANT ALL PRIVILEGES ON SCHEMA dbx_dev.dbx_platform TO `<application-id>`;
```

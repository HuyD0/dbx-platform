# Cloud setup — driving this repo without a local machine

Goal: make every change from a browser (or a phone), with GitHub Actions doing the
validation and deployment. Nothing below depends on a local toolchain.

Once this checklist is done, the loop is:

> comment `@claude ...` on an issue → Claude opens a PR → CI lints/tests/validates the
> bundle → you merge to `main` → `deploy.yml` deploys to the `prod` target.

## Auth model: keyless OIDC

There is **no Databricks service-principal secret**, and no client secret exists for the
app registration at all. GitHub Actions authenticates like this:

```
GitHub OIDC token  →  azure/login@v2 (federated credential)  →  az token
                   →  Databricks CLI (DATABRICKS_AUTH_TYPE=azure-cli)  →  workspace
```

The federated credential is bound to a subject ending in `:environment:production` (the
full string uses this repo's immutable OIDC prefix — see the note below). That is why
every workspace-touching job declares `environment: production` — **without it GitHub
mints a token with a different subject and Azure refuses the exchange.** It is also the
security boundary: `pull_request` runs never get a credential, so untrusted PR-branch
code cannot reach the workspace.

Same pattern as `agent-eval`, but a **separate identity** (`github-actions-dbx-platform`)
so a compromise here cannot touch agent-eval's SP — which holds Contributor at
subscription scope.

---

## Already provisioned

These exist; nothing to do.

| Thing | Value |
|---|---|
| Entra app / SP | `github-actions-dbx-platform` — appId `b74a6820-d0ac-454f-8c32-02141cba3c8a` |
| Federated credential | subject `repo:HuyD0@151226205/dbx-platform@1303537051:environment:production` (see note) |
| Workspace registration | registered via SCIM, member of the `admins` group |
| Workspace | `dbx-dev` — `https://adb-7405609799238491.11.azuredatabricks.net` |
| SQL warehouse | `09c77e5867b64a0d` (Serverless Starter Warehouse) |
| GitHub default branch | `main` |
| GitHub `production` environment | created, no protection rules |
| Repo secrets | `DATABRICKS_HOST`, `DATABRICKS_WAREHOUSE_ID`, `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` |

The **Deploy** workflow has run green end-to-end against the workspace, and the four
`[dbx-platform]` jobs are deployed. The keyless chain is proven, not just wired.

Workspace **admin** is required because the security job calls the token-management and
SCIM APIs — and, as it turned out, is also what authorizes the deploy (see "no Azure
RBAC" below). To avoid admin, strip the security job from the deployment.

> **Federated subject — immutable form.** This repo was created recently enough that
> GitHub issues OIDC tokens with the *immutable* subject prefix
> `repo:<owner>@<owner_id>/<repo>@<repo_id>`, not the plain `repo:<owner>/<repo>`. The
> federated credential is set to match the immutable subject GitHub actually sends. Older
> repos (e.g. `agent-eval`) still get the plain form — do not copy their subject string.
> Confirm what your repo sends with:
> `GET /repos/HuyD0/dbx-platform/actions/oidc/customization/sub` → `sub_claim_prefix`.

---

## What still needs you

Only two things remain, both in the GitHub web UI, and both are the `@claude` half — the
Databricks deploy pipeline is already live.

### 1. Install the Claude GitHub App

<https://github.com/apps/claude> → install it on `HuyD0/dbx-platform`.

**Required, and I could not do it for you** — installing a GitHub App needs an interactive
web authorization. The workflow file alone is not enough: without the App, `@claude` is
silently ignored — no run, no error, nothing to debug. It's the first thing Anthropic's
own troubleshooting tells you to check. It requests read & write on Contents, Issues, and
Pull requests.

### 2. Add the `CLAUDE_CODE_OAUTH_TOKEN` secret

You do **not** need an Anthropic API key or a console.anthropic.com account. Generate a
token from your existing Claude Pro/Max subscription instead:

```bash
claude setup-token      # in the Claude Code CLI — prints a long-lived OAuth token
```

Then repo → **Settings** → **Secrets and variables** → **Actions** → add
`CLAUDE_CODE_OAUTH_TOKEN` with that value. This bills `@claude` usage against your Claude
subscription, not a pay-per-use API bill.

(Prefer an API key anyway? Add `ANTHROPIC_API_KEY` instead and change the input in
`.github/workflows/claude.yml` from `claude_code_oauth_token` to `anthropic_api_key`.)

Everything else — the five `AZURE_*`/`DATABRICKS_*` secrets (identifiers, not
credentials), the `production` environment, and the default branch — is already set.

### Then verify the `@claude` loop

Open an issue, comment `@claude say hello and list the repo's CLI commands`, and confirm
Claude replies. (The Databricks side is already verified — Deploy #11 ran green through
every step and the jobs are in the workspace.)

### Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `azure/login` → "no matching federated identity record" | The federated subject doesn't match the token. Check the repo's `sub_claim_prefix` (immutable vs plain form, above) and that the `production` environment still exists and is named exactly. Entra matches case-sensitively. |
| `azure/login` → "No subscriptions found" | The SP has no Azure RBAC (by design). The workflow passes `allow-no-subscriptions: true` so login still mints the Entra token; if you removed that, restore it rather than granting a subscription role. |
| `current-user me` → literal `${var.workspace_host}` host error | A non-bundle CLI command was run from the bundle root, where it reads `databricks.yml` and doesn't interpolate variables. The workflow runs it from `runner.temp` for this reason. |
| Deploy → `no files match pattern: ./resources/dist/*.whl` | Wheel dependency path regressed. Job specs live in `resources/`, so the wheel (built at the bundle root) must be referenced as `../dist/*.whl`, not `./dist/*.whl`. |
| `@claude` does nothing at all — no run, no error | The Claude GitHub App isn't installed (step 1), or `claude.yml` isn't on the **default** branch (`main`). |
| Deploy waits on approval | A protection rule was added to the `production` environment. Remove it. |
| Scheduled job fails with `INSUFFICIENT_PERMISSIONS … USE SCHEMA on Schema 'system.<x>'` (exit 3) | The job's run-as principal — the service principal, since CI deployed the bundle — has no grant on that system schema. Workspace admin does not confer it. Run the grants below. |

### Required: grant system-table access to the service principal

The prod jobs are deployed by CI, so they **run as the service principal** (production
mode's default run-as is the deploying identity — with or without the explicit `run_as`
below). Workspace admin does **not** include Unity Catalog access to `system.*` schemas,
so without these grants every system-table task fails with
`INSUFFICIENT_PERMISSIONS … USE SCHEMA on Schema 'system.…'`.

Run as a metastore admin in a SQL editor (schemas must also be *enabled* on the
metastore first — setup.md §4):

```sql
GRANT USE SCHEMA, SELECT ON SCHEMA system.billing  TO `b74a6820-d0ac-454f-8c32-02141cba3c8a`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.access   TO `b74a6820-d0ac-454f-8c32-02141cba3c8a`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.lakeflow TO `b74a6820-d0ac-454f-8c32-02141cba3c8a`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.compute  TO `b74a6820-d0ac-454f-8c32-02141cba3c8a`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.query    TO `b74a6820-d0ac-454f-8c32-02141cba3c8a`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.serving  TO `b74a6820-d0ac-454f-8c32-02141cba3c8a`;
-- dashboards' helper schema. CREATE SCHEMA lets the dashboards-setup job create
-- main.dbx_platform on first run (it becomes owner, so it can then create the
-- functions/reference tables inside). If an admin pre-creates the schema instead,
-- the ALL PRIVILEGES grant covers those creates and CREATE SCHEMA is unnecessary.
GRANT USE CATALOG ON CATALOG main TO `b74a6820-d0ac-454f-8c32-02141cba3c8a`;
GRANT CREATE SCHEMA ON CATALOG main TO `b74a6820-d0ac-454f-8c32-02141cba3c8a`;
GRANT ALL PRIVILEGES ON SCHEMA main.dbx_platform TO `b74a6820-d0ac-454f-8c32-02141cba3c8a`;
```

The schema list matches what the jobs read (see the job table in runbook.md):
billing/lakeflow/compute/query for `cost-usage-report`, access for `security-audit`,
serving for `ml-serving-report`. The `dashboards-setup` job reads billing + access to
refresh its `workspace_reference` / `warehouse_reference` tables.

### Optional: run prod jobs as the service principal

Add to the `prod` target in `databricks.yml` so scheduled jobs stop running as a human:

```yaml
  prod:
    mode: production
    run_as:
      service_principal_name: b74a6820-d0ac-454f-8c32-02141cba3c8a
```

Do this only after step 4 passes, so a failure is unambiguous.

---

## Ways this stays laptop-free

| Task | How |
|---|---|
| Write code / open a PR | `@claude` on an issue, or claude.ai/code |
| Lint, test, build, validate bundle | `ci.yml` on every PR |
| Deploy to the workspace | `deploy.yml` on merge to `main`, or *Run workflow* |
| Ad-hoc admin command | Run the job in the Databricks UI, or add a `workflow_dispatch` job |
| Inspect a failing run | `@claude` on the PR — it has `actions: read` |

## Note on local CLI use

Use `gh` and the `databricks` CLI locally as normal — the cloud path exists so you
*don't have to*, not because anything is wrong with your machine.

(For agents: inside Claude Code's Bash sandbox both CLIs fail with
`tls: failed to verify certificate: x509: OSStatus -26276`. That is the sandbox blocking
the macOS trust daemon — `errSecInternalComponent`, not an untrusted cert — and it does
not reproduce in a normal terminal. `az`, Python, and `git` are unaffected.)

---

## Azure Cost Management access (the repo's first ARM RBAC grant)

The `azure-cost-pull` job calls the **Azure Cost Management Query API** at
subscription scope, which needs an Azure RBAC role. Everything else in this repo
deliberately holds **no ARM RBAC** (see the troubleshooting note above — the CI SP
authenticates to Databricks only). This grant is the one deliberate exception, and it
is read-only:

- **Role:** `Cost Management Reader` — can call the Query/Forecast/Cost Details APIs
  and view cost configuration; it can **not** create exports or budgets (that would
  need Cost Management Contributor, which is why the pipeline pulls via the Query API
  instead of managing storage exports).
- **Identity:** the **access connector's identity behind the UC service credential** the
  jobs resolve through `secrets.get_credential()` (docs/secrets.md has the access-connector
  recipe — the same construct used for Key Vault, so no new machinery). This workspace's
  connector is `dbx-dev-ac`
  (`/subscriptions/ea936670-dda1-4884-8467-49c225bf3e83/resourceGroups/rg-databricks-dbx-dev/providers/Microsoft.Databricks/accessConnectors/dbx-dev-ac`),
  a **system-assigned** identity (principal ID `d89926fd-c4bc-4d48-a52f-0119971d4a72`) —
  not a standalone UAMI; it already holds `Storage Blob Data Contributor` on the UC
  storage accounts, confirming it's the identity actually in use. `databricks credentials
  list-credentials` shows two service credentials pointing at this connector — `dbx_dev`
  and `learn_app_azure` (the latter belongs to the other project) — use `dbx_dev`. Keep the
  CI SP (`b74a6820-…`) RBAC-free; grant it too only if you want to run
  `dbx-platform azure-cost pull` locally under a non-user identity.

**Done for this deployment** (2026-07-18, via Azure MCP tools + Resource Graph, not the
`az` CLI — see the local-CLI note above):

```bash
az role assignment create \
  --assignee-object-id d89926fd-c4bc-4d48-a52f-0119971d4a72 \
  --assignee-principal-type ServicePrincipal \
  --role "Cost Management Reader" \
  --scope "/subscriptions/ea936670-dda1-4884-8467-49c225bf3e83"
```

Verified in place: role assignment
`9b8ef5f2-f528-4cea-b63e-5985d22dad45`, role `72fafb9e-0641-4937-9268-a91bfd8191a3`
(Cost Management Reader), scope = the subscription.

To reproduce this from scratch (a different subscription, a rotated connector, etc.):

```bash
# 1) The access connector's own identity (or the UAMI it wraps, if user-assigned)
CONNECTOR_PRINCIPAL_ID=$(az databricks access-connector show \
  -g <rg> -n <connector-name> --query identity.principalId -o tsv)

# 2) Read-only cost access on the subscription
az role assignment create \
  --assignee-object-id "$CONNECTOR_PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Cost Management Reader" \
  --scope "/subscriptions/$AZURE_SUBSCRIPTION_ID"
```

Then wire the bundle variables and deploy:

```bash
export BUNDLE_VAR_azure_subscription_id=ea936670-dda1-4884-8467-49c225bf3e83
export BUNDLE_VAR_azure_service_credential=dbx_dev
databricks bundle deploy -t prod
```

The job identity also needs `ACCESS` on the UC service credential, plus the
`main.dbx_platform` schema grants above (the ingest MERGEs into
`main.dbx_platform.azure_costs`).

First-run order (each step is also a plain CLI command if you prefer to run it
locally):

1. `azure-cost pull --days 365` — backfill the bill (one-off; the scheduled job
   re-pulls a 3-day window daily).
2. `forecast build-features` then `forecast train` — first training run registers the
   model and sets `@champion` (the promotion gate always promotes when there is no
   incumbent).
3. `cost_forecast_daily` job (or `forecast predict` + `forecast monitor`) — forecasts
   land in `main.dbx_platform.cost_forecasts`; the dashboard and the Console app's
   Azure Cost page light up.

| Symptom | Cause / fix |
|---|---|
| `azure-cost pull` → 403 with the Cost Management Reader hint | The role assignment above is missing, on the wrong identity, or still propagating (can take a few minutes). |
| `azure-cost pull` → `DefaultAzureCredential` errors inside a job | `--service-credential`/`BUNDLE_VAR_azure_service_credential` is empty, so the job fell back to a credential chain that only works locally. |
| `forecast train` → `need >= N days of features` | Not enough billing history ingested yet — run the 365-day backfill first. |

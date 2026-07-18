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
| Workspace registration | registered via SCIM; legacy `admins` membership must be removed after applying the scoped deployment grants |
| Workspace | `dbx-dev` — `https://adb-7405609799238491.11.azuredatabricks.net` |
| Shared SQL warehouse | `09c77e5867b64a0d` (Serverless Starter; preserved and no longer used by this bundle) |
| Mission Control warehouse | Bundle-owned `[dbx-platform] mission-control` (2X-Small serverless, five-minute auto-stop) |
| GitHub default branch | `main` |
| GitHub `production` environment | created, no protection rules |
| Repo secrets | `DATABRICKS_HOST`, `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` |
| Required repo variables | `DBX_PLATFORM_RUNTIME_EXECUTOR_SP` (runtime controller) and `DBX_PLATFORM_ACTION_EXECUTOR_SP` (allowlisted remediation executor) |

The keyless CI chain is proven. Mission Control adds a dedicated warehouse,
thirteen PAUSED report schedules, manual approval-gated training, and out-of-band
executor Jobs; enablement is proposal-only until the prerequisites below exist.

Workspace admin is not the target operating model. Deploy into the dedicated
application schema and apply the identity-specific matrix in
[service-principal.md](service-principal.md). If token listing/revocation
cannot be granted narrowly, keep that security pack proposal-only instead of
making the deployment or action executor a workspace admin.

> **Federated subject — immutable form.** This repo was created recently enough that
> GitHub issues OIDC tokens with the *immutable* subject prefix
> `repo:<owner>@<owner_id>/<repo>@<repo_id>`, not the plain `repo:<owner>/<repo>`. The
> federated credential is set to match the immutable subject GitHub actually sends. Older
> repos (e.g. `agent-eval`) still get the plain form — do not copy their subject string.
> Confirm what your repo sends with:
> `GET /repos/HuyD0/dbx-platform/actions/oidc/customization/sub` → `sub_claim_prefix`.

---

## What still needs you

Mission Control requires two platform prerequisites in addition to the two
GitHub/Claude items below:

- create `dbx-platform-approvers` and add the humans allowed to approve plans;
- provision separate least-privileged runtime and action executors, register
  both in the workspace, grant the runtime permissions in
  [runbook.md](runbook.md#safe-hibernate), grant the action executor
  only its enabled action-pack permissions, and set
  `DBX_PLATFORM_RUNTIME_EXECUTOR_SP` plus
  `DBX_PLATFORM_ACTION_EXECUTOR_SP` in the GitHub production environment.

For a temporary proposal-only bootstrap, both variables may reference the
deployment identity only when `DBX_PLATFORM_ACTIONS_ENABLED=false` and
`DBX_PLATFORM_ALLOW_SHARED_EXECUTOR_SP=true`. The workflows fail closed if
actions are enabled while the identities are shared. Replace the shared value
with two dedicated least-privileged identities and remove the exception before
controlled action enablement.

The deploy workflow fails instead of substituting another identity when either
variable is absent. It runs idempotent control-plane migrations under the CI
deployment identity, leaves schedules PAUSED, and creates only a reconciliation
proposal.

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

Everything else — the four `AZURE_*`/`DATABRICKS_*` secrets (identifiers, not
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
| Scheduled job fails with `INSUFFICIENT_PERMISSIONS … USE SCHEMA on Schema 'system.<x>'` | The job's run-as principal — the service principal, since CI deployed the bundle — has no grant on that system schema. Workspace admin does not confer it. Run the grants below. |

### Required: apply the report/deployment grants

Production report Jobs run as their configured report/deployment identity.
Workspace admin does not confer Unity Catalog access to `system.*`. Grant only
the schemas enabled for installed evidence packs:

```sql
GRANT USE SCHEMA, SELECT ON SCHEMA system.billing  TO `dbx-platform-reporters`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.access   TO `dbx-platform-reporters`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.lakeflow TO `dbx-platform-reporters`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.compute  TO `dbx-platform-reporters`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.query    TO `dbx-platform-reporters`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.serving  TO `dbx-platform-reporters`;
```

Pre-create `main.dbx_platform` and make the deployment group its owner as shown
in [setup.md](setup.md); only the unscheduled `schema_migrations` bootstrap
performs DDL. Do not grant the CI principal `CREATE SCHEMA` on `main` or
`ALL PRIVILEGES` on the application schema. Table-level app, human, runtime,
action-executor, and reporter grants are in
[service-principal.md](service-principal.md).

### Optional: run read-only prod jobs as the CI service principal

The power/action executor Jobs already use the dedicated runtime executor.
Optionally add this to the `prod` target so read-only scheduled reports also run
under CI rather than the resource owner:

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
| Stateful/costly manual run | Create and approve an exact `run-job` action in Mission Control |
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
`dbx_dev.dbx_platform` schema grants above (the ingest MERGEs into
`dbx_dev.dbx_platform.azure_costs`).

First-run order (stateful/costly manual runs go through Action Center):

1. For a historical backfill, make a reviewed temporary bundle change from the
   Job’s 3-day window to 365 days, deploy it, plan/approve the exact changed
   Job, then restore/redeploy the 3-day window. Otherwise let scheduled
   ingestion accumulate.
2. Plan and approve the manual `cost-forecast-train` Job, which builds features,
   registers the first model, and sets `@champion` (training/promotion has no
   schedule).
3. Let `cost-forecast-daily` run on schedule or plan/approve that exact Job.
   Forecasts land in `main.dbx_platform.cost_forecasts`.

| Symptom | Cause / fix |
|---|---|
| `azure-cost pull` → 403 with the Cost Management Reader hint | The role assignment above is missing, on the wrong identity, or still propagating (can take a few minutes). |
| `azure-cost pull` → `DefaultAzureCredential` errors inside a job | `--service-credential`/`BUNDLE_VAR_azure_service_credential` is empty, so the job fell back to a credential chain that only works locally. |
| Forecast training → `need >= N days of features` | Not enough approved/scheduled billing history exists; complete the backfill action first. |

---

## Azure Resource Graph access (AI catalog)

The `ai-catalog-sync` job inventories Azure AI Foundry / Azure OpenAI
accounts, model deployments, and the RBAC role assignments that grant access
to them, via **Azure Resource Graph** (plus a per-account ARM fallback for
deployments). ARG only returns resources the caller can read, so this is the
repo's second read-only ARM RBAC grant, same identity and pattern as Cost
Management Reader above:

- **Role:** `Reader` — sufficient for ARG queries, resource enumeration, and
  reading role assignments; it cannot change anything.
- **Identity:** the access connector's system-assigned identity behind the
  `dbx_dev` UC service credential (principal ID
  `d89926fd-c4bc-4d48-a52f-0119971d4a72`) — the same identity that holds Cost
  Management Reader.
- **Scope:** one grant **per subscription listed in
  `BUNDLE_VAR_azure_ai_subscriptions`** (least privilege, recommended):

```bash
az role assignment create \
  --assignee-object-id d89926fd-c4bc-4d48-a52f-0119971d4a72 \
  --assignee-principal-type ServicePrincipal \
  --role "Reader" \
  --scope "/subscriptions/<subscription-id>"   # repeat per listed subscription
```

Leaving `azure_ai_subscriptions` empty switches the sync to "every
subscription the identity can read" — useful with a management-group-scope
grant instead:

```bash
az role assignment create \
  --assignee-object-id d89926fd-c4bc-4d48-a52f-0119971d4a72 \
  --assignee-principal-type ServicePrincipal \
  --role "Reader" \
  --scope "/providers/Microsoft.Management/managementGroups/<mg-id>"
```

Then wire the bundle variables and deploy:

```bash
export BUNDLE_VAR_azure_ai_subscriptions=ea936670-dda1-4884-8467-49c225bf3e83
export BUNDLE_VAR_azure_service_credential=dbx_dev
databricks bundle deploy -t prod
```

The job identity also needs `ACCESS` on the UC service credential and the
`ai_model_catalog` / `ai_model_access` grants in
[service-principal.md](service-principal.md). The companion
`ai-monitor-rollup` job needs no ARM RBAC at all — it reads
`system.serving.*` (already in the reporter grant set) and, when the Beta is
enabled, `system.ai_gateway.usage`.

| Symptom | Cause / fix |
|---|---|
| `ai-catalog sync` → 403 with the Reader hint | The Reader assignment above is missing on a listed subscription, or still propagating. |
| Sync succeeds but finds zero Azure accounts | The identity has no Reader grant on the listed subscriptions (ARG silently returns only readable resources), or the subscription list is wrong. |
| Role assignments show DIRECT/RG/SUBSCRIPTION but never MANAGEMENT_GROUP | Expected when scoping to a subscription list only if the MG has no AI-relevant assignments; the sync already widens the scope filter (`AtScopeAboveAndBelow`) to include inherited ones. |
| `DefaultAzureCredential` errors inside the job | `--service-credential`/`BUNDLE_VAR_azure_service_credential` is empty, so the job fell back to a credential chain that only works locally. |

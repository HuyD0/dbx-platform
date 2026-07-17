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

The federated credential is bound to the subject
`repo:HuyD0/dbx-platform:environment:production`. That is why every workspace-touching
job declares `environment: production` — **without it GitHub mints a token with a
different subject and Azure refuses the exchange.** It is also the security boundary:
`pull_request` runs never get a credential, so untrusted PR-branch code cannot reach the
workspace.

Same pattern as `agent-eval`, but a **separate identity** (`github-actions-dbx-platform`)
so a compromise here cannot touch agent-eval's SP — which holds Contributor at
subscription scope.

---

## Already provisioned

These exist; nothing to do.

| Thing | Value |
|---|---|
| Entra app / SP | `github-actions-dbx-platform` — appId `b74a6820-d0ac-454f-8c32-02141cba3c8a` |
| Federated credential | `repo:HuyD0/dbx-platform:environment:production` |
| Workspace registration | registered via SCIM, member of the `admins` group |
| Workspace | `dbx-dev` — `https://adb-7405609799238491.11.azuredatabricks.net` |
| SQL warehouse | `09c77e5867b64a0d` (Serverless Starter Warehouse) |

Workspace **admin** is required because the security job calls the token-management and
SCIM APIs. To avoid granting it, strip the security job from the deployment.

---

## What still needs you

All of it is in the GitHub web UI — no laptop, no CLI.

### 1. Set `main` as the default branch

Repo → **Settings** → **General** → *Default branch* → `main`.

This matters more than it looks:

- `ci.yml` / `deploy.yml` trigger on `push: branches: [main]`. If the default branch is
  something else, a merged PR lands there and **the deploy never fires.**
- GitHub runs `issue_comment` workflows **only from the default branch's copy of the
  file**, so `@claude` will not respond until `claude.yml` is on the default branch.

### 2. Install the Claude GitHub App

<https://github.com/apps/claude> → install it on `HuyD0/dbx-platform`.

**Required** — the workflow file alone is not enough. Without the App, `@claude` is
silently ignored: no run, no error, nothing to debug. It's the first thing Anthropic's
own troubleshooting tells you to check. It requests read & write on Contents, Issues,
and Pull requests.

### 3. Create the `production` environment

Repo → **Settings** → **Environments** → **New environment** → name it exactly
`production`. **Add no protection rules** — a required reviewer here would make every
merge-to-`main` deploy wait for manual approval, which is the opposite of the
auto-deploy loop.

Not cosmetic: the federated credential's subject embeds this name
(`repo:HuyD0/dbx-platform:environment:production`), and Entra matches it
**case-sensitively**. Skip it, misname it, or change the repo/org casing and every
Azure login fails with an opaque "no matching federated identity" error.

### 4. Add the repository secrets

Repo → **Settings** → **Secrets and variables** → **Actions**:

| Secret | Value |
|---|---|
| `DATABRICKS_HOST` | `https://adb-7405609799238491.11.azuredatabricks.net` |
| `DATABRICKS_WAREHOUSE_ID` | `09c77e5867b64a0d` |
| `AZURE_CLIENT_ID` | `b74a6820-d0ac-454f-8c32-02141cba3c8a` |
| `AZURE_TENANT_ID` | `7f6a2cf9-5e4e-46ae-95d4-74016c1df1a6` |
| `AZURE_SUBSCRIPTION_ID` | `ea936670-dda1-4884-8467-49c225bf3e83` |
| `ANTHROPIC_API_KEY` | <https://console.anthropic.com> → API keys |

The four `AZURE_*`/`DATABRICKS_*` values are identifiers, not credentials — none of them
grant access on their own. They live in secrets to match the `agent-eval` convention.
`ANTHROPIC_API_KEY` is the one real secret; the Claude action also supports keyless WIF
if you want to remove it later.

### 5. Verify (do not skip)

1. Actions → **Deploy** → *Run workflow* on `main`.
2. The **Confirm the authenticated identity** step should print
   `github-actions-dbx-platform`. That is the proof OIDC → Databricks works.
3. Confirm `databricks bundle deploy -t prod` succeeds.
4. Open an issue, comment `@claude say hello and list the repo's CLI commands`, and
   confirm Claude replies.

Until then CI and Deploy are **red on `main`, which is expected** — the workflows no
longer self-skip when unconfigured, so a deploy that cannot authenticate fails loudly
instead of exiting green having done nothing. They go green once the secrets exist.

### Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `azure/login` → "no matching federated identity record" | The `production` environment is missing/misnamed, or the org/repo casing differs from `repo:HuyD0/dbx-platform:environment:production`. Entra matches the subject case-sensitively. |
| `current-user me` → 403 / auth denied | The SP is registered via SCIM and is a workspace `admins` member, which *should* be sufficient for the `azure-cli` data-plane path — but this is untestable before the first run (the SP is federated-only; there is no secret to exercise locally). If it fails, grant it Azure RBAC on the workspace resource: `az role assignment create --assignee b74a6820-d0ac-454f-8c32-02141cba3c8a --role Contributor --scope /subscriptions/ea936670-dda1-4884-8467-49c225bf3e83/resourceGroups/rg-databricks-dbx-dev/providers/Microsoft.Databricks/workspaces/dbx-dev` |
| `@claude` does nothing at all — no run, no error | The Claude GitHub App isn't installed (step 2), or `claude.yml` isn't on the **default** branch. |
| Deploy waits on approval | A protection rule was added to the `production` environment. Remove it. |

### 6. Optional: run prod jobs as the service principal

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

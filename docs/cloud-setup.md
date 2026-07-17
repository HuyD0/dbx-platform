# Cloud setup — driving this repo without a local machine

Goal: make every change from a browser (or a phone), with GitHub Actions doing the
validation and deployment. Nothing below depends on a local toolchain.

Once this checklist is done, the loop is:

> comment `@claude ...` on an issue → Claude opens a PR → CI lints/tests/validates the
> bundle → you merge to `main` → `deploy.yml` deploys to the `prod` target.

Everything here is a **one-time** setup. Steps 1–5 must be done by a human, because
they mint credentials.

---

## 1. Set `main` as the default branch

Repo → **Settings** → **General** → *Default branch* → switch to `main`.

This matters more than it looks:

- `ci.yml` and `deploy.yml` trigger on `push: branches: [main]`. If the default branch
  is something else, a merged PR lands there and **the deploy never fires.**
- GitHub runs `issue_comment`-triggered workflows **only from the default branch's copy
  of the file.** `claude.yml` will not respond to `@claude` until it is on the default
  branch.

## 2. Create the Databricks service principal

Account console (<https://accounts.azuredatabricks.net>) → **User management** →
**Service principals** → **Add service principal**, e.g. `dbx-platform-ci`.

Then grant it workspace **Admin** (workspace admin console → *Identity and access* →
*Service principals*). Admin is required because the security job calls the
token-management and SCIM APIs. See [service-principal.md](service-principal.md) for the
non-admin variant.

## 3. Generate an OAuth secret

Account console → the service principal → **Secrets** → **Generate secret**.
Record the **client ID** (application ID) and the **secret** — the secret is shown once.

## 4. Get a SQL warehouse ID

Workspace → **SQL Warehouses** → pick one → the ID is in the URL / *Connection details*.
The dashboards and system-table job tasks need it; the bundle has no default.

## 5. Add the repository secrets

Repo → **Settings** → **Secrets and variables** → **Actions** → *New repository secret*:

| Secret | Value | Used by |
|---|---|---|
| `DATABRICKS_HOST` | `https://adb-7405609799238491.11.azuredatabricks.net` | ci, deploy |
| `DATABRICKS_CLIENT_ID` | service principal application ID | ci, deploy |
| `DATABRICKS_CLIENT_SECRET` | the OAuth secret from step 3 | ci, deploy |
| `DATABRICKS_WAREHOUSE_ID` | warehouse ID from step 4 | ci, deploy |
| `ANTHROPIC_API_KEY` | <https://console.anthropic.com> → API keys | claude |

`ci.yml` and `deploy.yml` self-skip while `DATABRICKS_HOST` is unset, so they go green
without ever touching the workspace. **A green CI run does not prove the workspace
connection works** — that only starts being tested once these secrets exist.

## 6. Verify (do not skip)

1. Actions tab → **Deploy** → *Run workflow* on `main`.
2. Confirm the run does **not** print `Deploy skipped:` — that string means the secrets
   aren't being read.
3. Confirm `databricks bundle deploy -t prod` succeeds.
4. Open an issue, comment `@claude say hello and list the repo's CLI commands`, and
   confirm Claude replies.

## 7. Optional but recommended: run prod jobs as the service principal

Add to the `prod` target in `databricks.yml` so scheduled jobs stop running as a human:

```yaml
  prod:
    mode: production
    run_as:
      service_principal_name: <application-id>
```

Do this only after step 6 passes, so a failure is unambiguous.

---

## Ways this stays laptop-free

| Task | How |
|---|---|
| Write code / open a PR | `@claude` on an issue, or claude.ai/code |
| Lint, test, build, validate bundle | `ci.yml` on every PR |
| Deploy to the workspace | `deploy.yml` on merge to `main`, or *Run workflow* |
| Ad-hoc admin command | Run the job in the Databricks UI, or add a `workflow_dispatch` job |
| Inspect a failing run | `@claude` on the PR — it has `actions: read` |

## Note on local CLI failures

On some machines `gh` and the `databricks` CLI fail with
`tls: failed to verify certificate: x509: OSStatus -26276`. That is a local macOS
cert-store problem affecting Go binaries; plain `git` is unaffected. It has no bearing
on CI — GitHub's runners have a clean cert store. It's a reason to prefer this cloud
path, not something that needs fixing first.

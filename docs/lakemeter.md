# Native LakeMeter integration

LakeMeter OSS runs inside the existing Platform Console at
`/cost/estimator/*`. It is not an iframe or a second Databricks App. The
Platform Console owns navigation, identity verification, theme, responsive
layout, accessibility, and the single contextual assistant launcher.

## Boundaries

- `vendor/lakemeter` is an untouched snapshot of one stable upstream tag.
  `integrations/lakemeter/upstream.lock.json` records the tag, commit, archive
  checksum, vendor-tree checksum, schema version, pricing version, and license
  checksum.
- `integrations/lakemeter/frontend` compiles the upstream React pages as a
  separate bundle. The host mounts it in Shadow DOM at the
  `/cost/estimator` basename and maps LakeMeter tokens to Mission Control.
- `apps/platform-console/backend/lakemeter_*` lazily mounts selected upstream
  routers under `/api/v1`. User-admin, debug, standalone shell, CORS, and
  static catch-all surfaces are never mounted.
- Every `/api/v1` request crosses Platform Console's forwarded-token/SCIM
  verifier. Spoofable identity headers are removed and reconstructed from the
  verified actor. LakeMeter's own owner/share filters then scope estimates.
- The App uses only the Lakebase OAuth binding and the attached model endpoint.
  It has no Databricks target-resource mutation capability and performs no
  database DDL.

## Initial rollout

Set a dedicated service-principal application ID:

```bash
export BUNDLE_VAR_lakemeter_migration_executor_service_principal_name=<client-id>
```

The first rollout has three deliberately separate phases:

1. Confirm the configured Lakebase project has a ready `production` branch and
   `primary` endpoint. Review and approve the companion-resource plan in
   `resources/lakemeter.yml`, then apply only its `postgres_roles` and
   `postgres_databases` selections. The companion must not manage the shared
   project, branch, or endpoint, and these resources remain excluded from the
   normal application deploy.
2. After the database exists, deploy the reviewed `lakemeter-database` App
   resource binding in `resources/app.yml`. The attachment supplies
   `PGHOST`, `PGDATABASE`, and `PGUSER` to the App and grants its identity an
   OAuth-only connection; it neither provisions Lakebase nor applies DDL.
3. Deploy the isolated assets and unscheduled migration Job, then in Action
   Center create and approve an exact `run-job` action for
   `lakemeter-schema-migrations`. The general action executor revalidates that
   immutable approval and starts the target Job; the Job itself runs as the
   dedicated LakeMeter migration identity.

Until all three phases complete, `/api/lakemeter/status` reports the missing or
stale schema and the Estimator tab shows a setup/maintenance state. A normal
deployment attaches the already-provisioned database but never selects
Lakebase infrastructure and never starts the migration Job.
Production deployment checks that exact database before building or changing
normal resources, so an incomplete companion rollout fails early without
partially updating the App or Jobs.

The current PoC reuses `learn-app-sync-dev-1/production` while keeping
LakeMeter data in its own `lakemeter` database. This shares project compute and
project-level administration with Learn App, but not application tables,
database ownership, or schema migrations.

The migration is transactional. It reads schema/function/reference definitions
from the pinned snapshot, reconciles only additive or replace-in-place objects,
loads the pinned pricing bundle, verifies a calculation, grants the App
`SELECT/INSERT/UPDATE/DELETE`, sequence usage, and function execution, then
writes the required schema version. The App role receives no schema or database
create privilege.

## Local build

```bash
uv sync --extra dev

npm --prefix apps/platform-console/frontend ci
npm --prefix apps/platform-console/frontend run build

npm --prefix integrations/lakemeter/frontend ci
npm --prefix integrations/lakemeter/frontend run typecheck
npm --prefix integrations/lakemeter/frontend run build

uv run python scripts/check_lakemeter_compat.py
uv run python scripts/stage_lakemeter_app.py
```

Without a Lakebase binding, the backend remains healthy and the Estimator
shows setup required. For local integration testing only, `DATABASE_URL` may
point to a disposable PostgreSQL database; production never uses a password
fallback.

## Upstream updates

`.github/workflows/lakemeter-update.yml` checks stable release tags weekly. A
new release replaces the selected pristine snapshot, regenerates compressed
pricing assets and the lock, then runs backend, API, database-input, route,
dependency, frontend, Shadow DOM, test, and wheel checks. It opens a draft PR
whether compatibility passes or fails; failed checks keep the PR blocked until
an adapter is deliberately updated.

To rehearse an update:

```bash
uv run python scripts/update_lakemeter.py --tag vX.Y.Z
uv run python scripts/check_lakemeter_compat.py
```

Never edit `vendor/lakemeter` directly. Adapter changes belong under
`integrations/lakemeter`, `apps/platform-console/backend`, or
`src/dbx_platform/lakemeter_migrations.py`. Preserve `LICENSE.md` and
`NOTICE.md`; the local adapter is a modified integration layer, not an
upstream LakeMeter release.

## Failure modes

| Status or symptom | Operator action |
|---|---|
| `database_not_configured` | Complete the companion database rollout, then add and verify the App's `lakemeter-database` resource binding. |
| `schema_migration_required` | Review and approve the exact migration Job run. |
| `database_unavailable:*` | Check endpoint state and the dedicated OAuth role; do not add a password secret. |
| `frontend_not_built` | Re-run the host build, isolated build, and staging steps. |
| Assistant 401/503 | Confirm the attached serving endpoint and the App's `CAN_QUERY` resource. Estimates/calculators remain available. |
| Excel export failure | Inspect the `/api/v1/export` response and App logs; generic JSON masking is intentionally bypassed for this binary contract. |

The raw VM pricing CSV stays in the repository snapshot for provenance but is
not staged into the App because it exceeds the individual App file limit. The
frontend uses the authenticated VM pricing API instead.

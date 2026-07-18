# Mission Control runbook

## Operating rule

The only mutation path is:

`durable plan → authorized approval → dedicated executor → verification event`

The app and assistant can investigate and propose. They cannot execute target
APIs. Legacy `--apply`, direct dashboard setup, direct wheel publication, and
direct model/agent deployment are disabled. Resource deletion is not an
allowlisted v1 action.

Scheduled jobs may read platform sources and append findings, cost/usage
ledger rows, forecasts, and audit telemetry. Budget/configuration changes,
training/model promotion, manual stateful job runs, remediation, Hibernate,
and Wake always require approval.

## Action lifecycle

Normal:

`AWAITING_APPROVAL → APPROVED → EXECUTING → VERIFYING → SUCCEEDED`

Terminal/retry outcomes:

`REJECTED`, `EXPIRED`, `STALE`, `FAILED`, `ROLLED_BACK`

Every action stores the canonical plan JSON and SHA-256 hash, exact targets,
resource versions/preconditions, before/after state, impact, rollback,
verification, proposer, workspace/environment, 15-minute expiry, and
single-use idempotency key. Every approval stores the same plan hash, verified
approver identity/role, decision, timestamp, and typed confirmation when
required. Execution and verification produce append-only events.

Any payload change, target drift, expiry before executor claim, replay, missing
SCIM identity, lost approver-group membership, unavailable audit storage, or
failed precondition invalidates the action without a target mutation.

## Human approval

1. Open **Action Center → Awaiting Approval**.
2. Confirm workspace/environment, exact target count, before/after state,
   source freshness, blast radius, rollback, and verification.
3. For medium/high risk, type the displayed action and target count.
4. Approve or reject. One current member of `dbx-platform-approvers` is
   sufficient and may approve their own proposal.
5. Follow Activity through execution and verification. Do not retry by
   resubmitting a payload; create a fresh plan after `STALE`, `EXPIRED`, or a
   changed target.

PAT revocation explicitly has no rollback. Cluster termination is recoverable;
permanent deletion is unsupported.

## Proposal-only enablement

Keep bundle variable `actions_enabled=false` until all of these pass:

1. `schema_migrations` succeeds under the deployment identity.
2. One complete reporting cycle writes canonical findings and cost ledgers.
3. Source-health cards show their real freshness/coverage; unavailable preview
   sources are visible rather than silently omitted.
4. `dbx-platform-approvers` membership resolves through Databricks user
   authorization/SCIM.
5. Runtime and action executors are distinct identities with the grants in
   [service-principal.md](service-principal.md).
6. Spoofed identity, altered hash, unauthorized approval, expiry, replay,
   target drift, and missing audit storage tests fail without mutation.
7. A valid low-risk test action executes once and produces a complete plan,
   approval, execution, and verification trail.

Then set `BUNDLE_VAR_actions_enabled=true` through a reviewed deployment.
Turning on this flag only permits approval/executor submission; it does not
bypass any durable checks.

## Safe Hibernate

The exact managed scope is generated from bundle output:

- Platform Console app;
- thirteen declared schedules;
- dedicated `[dbx-platform] mission-control` 2X-Small serverless warehouse.

Protected/out of scope:

- shared Starter and every unrelated warehouse;
- unscheduled `power-controller`, `action-executor`, and `schema_migrations`;
- protected manual `cost-forecast-train`;
- dashboards, UC data, models, storage/networking, workspace, and unrelated
  projects.

The controller never discovers targets by substring, tag, or broad workspace
scan.

### Plan and execute Hibernate

Use **Workflows → `[dbx-platform] power-controller`**:

1. Run `operation=plan-hibernate`.
2. Review resources to stop, already-stopped resources, exclusions, active
   runs/queries, dependencies, estimated idle savings, retained data, wake
   procedure, inverse state, expiry, hash, and confirmation phrase.
3. Before expiry, rerun with:
   - `operation=execute-hibernate`
   - `plan_id=<reviewed action id>`
   - `plan_hash=<reviewed hash>`
   - `confirmation=<exact displayed phrase>`

Execution:

1. Persist exact before state and inverse Wake plan.
2. Pause only schedules previously unpaused.
3. Wait up to 15 minutes for owned runs and dedicated-warehouse statements.
4. If activity remains, abort and restore schedules/runtime state. Cancellation
   requires a separate action and is unsupported in v1.
5. Stop the dedicated warehouse.
6. Persist checkpoints and desired `SLEEPING`.
7. Stop the app last.

On partial failure the controller restores captured state where possible and
records the exact result. Drift becomes `STALE`; there is no best-effort
mutation.

## Wake while the app is stopped

Use the same out-of-band controller in the Jobs UI:

1. Run `operation=plan-wake`.
2. Review the exact 15-minute plan/hash.
3. Rerun with `operation=execute-wake`, plan ID, plan hash, and exact
   confirmation.

The controller verifies the launcher’s current approver-group membership,
starts the dedicated warehouse, starts and health-checks the currently
deployed app revision, then restores only schedules enabled before Hibernate.
Repeated Wake/Hibernate calls are idempotent.

The controller does not deploy source. Releasing a different app revision is a
separate reviewed deployment.

## Deployment reconciliation

Every bundle schedule declares `pause_status: PAUSED`; the app and dedicated
warehouse declare `started: false`.

CI performs:

1. build wheel and frontend;
2. validate/deploy the bundle;
3. run unscheduled `schema_migrations` on serverless Spark;
4. run `power_controller operation=reconcile`.

The migration is the sole bootstrap path for schemas, tables, and dashboard
helper functions. It does not start the managed SQL warehouse. Reconciliation
reads durable desired state and either reports `ALREADY_RECONCILED` or creates
an `AWAITING_APPROVAL` plan. CI never executes it.

Deploying while desired state is `SLEEPING` leaves the toolkit asleep.

## Protected forecast training

`cost-forecast-train` is unscheduled and runs as the action executor identity.
It is exact-bound into the app as a governed manual Job but is absent from the
Hibernate inventory.

To train/promote:

1. Create a `run-job` action for the exact bundle Job ID.
2. Review the full Job settings hash and run-as identity.
3. Approve the plan.
4. The action executor revalidates the Job, launches it once with an
   idempotency token, and records the resulting Databricks run ID.
5. Before MLflow logging, registration, or alias changes, the task re-reads
   `action_requests`, `action_approvals`, and `action_events`; recomputes the
   plan hash; and verifies exact workspace, environment, Job ID, and current
   run ID.

A manual rerun, copied action parameters, old successful action, renamed Job,
changed task/compute settings, or mismatched run ID fails before training.

Direct `dbx-platform forecast train` is therefore blocked unless running as
the exact executor-launched action.

## Findings and remediations

### Stale clusters

The scheduled check proposes recoverable termination for running clusters
over threshold or without auto-termination. Old terminated clusters produce a
`review-retention` finding only. They are never deleted by v1.

Create a `stale-clusters` plan in Action Center. The executor re-reads state and
can terminate only exact approved cluster IDs that remain eligible.

### Orphaned jobs

The action pauses existing schedules/triggers; it never deletes Jobs.
Reassign ownership, then use a new approved action to change schedule state.

### PATs

“Expiring soon” is advisory. A `token-revoke` action can contain only exact
over-age PAT findings and has no rollback. Notify owners first. This pack
requires a powerful token-management permission and should remain
proposal-only unless its risk is explicitly accepted.

### Policy drift

Git remains the source for managed policy JSON. The executor creates/updates
only exact approved policies and never deletes unmanaged policies. Any new
drift after planning produces `STALE`.

### Budgets

Budget alerts are autonomous/read-only. A budget change uses
`configure-budget`, stores exact before/desired state, and is applied by the
action executor to `llm_budgets`. Alerts never stop endpoints or change model
routing.

### ML/serving

Audit only customer-managed/configurable endpoints and AI Gateway services.
Built-in pay-per-token endpoints are excluded from findings that cannot apply
to them. Serving reconfiguration, endpoint/model deletion, model promotion,
and agent deployment are not general executor actions in v1.

`agents/platform_agent/deploy_agent.py` intentionally exits without logging,
registering, or deploying. Add a narrowly scoped, tested model-deploy action
before enabling it.

### AI catalog & monitoring

The `ai-catalog-sync` and `ai-monitor-rollup` schedules only read and append;
every remediation below is a manual owner action:

- **`disable-key-auth (manual)`** — an Azure AI account allows API-key access
  (`disableLocalAuth=false`), so model calls cannot be attributed to an
  identity. The account owner sets `disableLocalAuth=true` (Entra-only auth)
  after confirming no caller still uses keys; roll keys first if unsure.
- **`review-model-grant (manual)` / `review-endpoint-acl (manual)`** — a
  broad group (`account users`, `users`) can invoke a model or query an
  endpoint. The object owner narrows the grant to the intended team group.
- **`narrow-role-scope (manual)`** — an AI-capable RBAC role is assigned at
  subscription or management-group scope. Reassign it at the resource or
  resource-group scope instead.
- **`enable-usage-tracking (manual)`** — an endpoint bills serving cost but
  emits no usage telemetry. The endpoint owner enables AI Gateway usage
  tracking so production traffic becomes observable.

## LLM Cost & Value operations

`llm-cost-rollup` writes provider-aware daily cost and hourly usage ledgers.
Interpret labels literally:

- `Azure actual`: Azure billing, including later adjustments;
- `Databricks list`: usage joined to list prices, not an invoice;
- `provider estimate`: AI Gateway/provider estimate, never silently combined
  with actual billed cost.

Do not add currencies without a documented conversion source/rate/time.
Request telemetry allocates billed totals to workloads but does not claim
invoice-accurate per-request cost. Keep an explicit `unallocated/uncovered`
bucket.

When preview sources such as AI Gateway usage/cost tables are absent, the UI
must show `unavailable` and the fallback source. Verify actual/detail
reconciliation, request/token coverage, currency, freshness, and the true
retention boundary before trusting optimization findings.

Suggested investigation order:

1. spend anomaly and late billing adjustments;
2. retry storms or agent loops;
3. context/input growth and output growth;
4. expensive-model drift;
5. cache effectiveness;
6. idle customer-managed endpoints;
7. missing/unallocated attribution;
8. cost per successful task versus quality/latency.

All optimization changes still require approval and must state savings range
plus quality/latency risk.

## Dependency health

The scheduled `dashboard-dependency-health` Job runs `dashboards health` and
performs only `SHOW` queries. Missing helpers are repaired only by the next
reviewed deployment’s `schema_migrations` run.

Do not run `dashboards setup`; it is a disabled compatibility command.

System-table and preview-source failures should be shown as dependency-health
states with source, freshness, and setup guidance. Mission Control must not
render raw backend/SQL exceptions.

## Audit and incident response

For a failed action, collect:

- action ID/hash and status;
- exact workspace/environment;
- proposal/approval/executor identities;
- `action_events` in timestamp order;
- expected/current resource version;
- mutation checkpoint and verification;
- rollback outcome;
- relevant Job run ID and task output.

Do not edit an action record to retry it. Preserve it and create a fresh plan.
If audit writes fail, keep actions disabled until storage is restored and the
preflight append/update checks pass.

Every successful action emits an immediate `IMPACT_MEASUREMENT` verification
checkpoint with a 24-hour observation window. The existing daily LLM ledger
schedule and weekly `platform-digest` schedule both run
`report impact-followup`; the weekly run follows a canonical finding refresh.
If an exact target has not appeared in a fresh finding yet, the collector
records `IMPACT_FOLLOW_UP_PENDING` and retries instead of permanently storing
an empty outcome. It appends `IMPACT_FOLLOW_UP_MEASURED` once target evidence
is available or the seven-day source-correlation grace period has elapsed.
Financial or SLO attribution that the available sources cannot prove remains
explicitly `UNATTRIBUTED`/unavailable; it is never filled with an estimate.

For suspected identity spoofing or executor credential exposure:

1. disable action submission (`actions_enabled=false`);
2. stop/disable the affected executor credential;
3. preserve action/audit tables and workspace audit logs;
4. review executor grants and recent exact targets;
5. rotate credentials where applicable;
6. restore proposal-only mode and repeat negative acceptance tests.

## Validation commands

```bash
ruff check .
pytest
python -m build --wheel
databricks bundle validate -t dev \
  --var runtime_executor_service_principal_name=<runtime-client-id> \
  --var action_executor_service_principal_name=<action-client-id>
```

Frontend:

```bash
cd apps/platform-console/frontend
npm ci
npm test
npm run build
```

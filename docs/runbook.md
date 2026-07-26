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
training/model promotion, manual stateful job runs, and remediation always
require approval.

## Action lifecycle

Normal:

`AWAITING_APPROVAL → APPROVED → EXECUTING → VERIFYING → SUCCEEDED`

Terminal/retry outcomes:

`REJECTED`, `EXPIRED`, `STALE`, `FAILED`, `ROLLED_BACK`

Every action stores the canonical plan JSON and SHA-256 hash, exact targets,
resource versions/preconditions, before/after state, impact, rollback,
verification, proposer, workspace/environment, 15-minute expiry, and
single-use idempotency key. Every approval stores the same plan hash, verified
approver identity/role, decision, and timestamp. The UI requires a separate
explicit confirmation after the approval click. Execution and verification
produce append-only events.

Any payload change, target drift, expiry before executor claim, replay, missing
SCIM identity, lost approver-group membership, unavailable audit storage, or
failed precondition invalidates the action without a target mutation.

## Human approval

1. Open **Review & Approve → Needs your review**.
2. Confirm workspace/environment, exact target count, before/after state,
   source freshness, blast radius, rollback, and verification.
3. Select Approve, then confirm the decision in the separate confirmation step.
4. Approve or reject. One current member of `dbx-platform-approvers` is
   sufficient and may approve their own proposal.
5. Follow **In progress** through execution and verification, then use
   **History** for the final outcome. Do not retry by
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
5. Evidence-job and action executors are distinct identities with the grants in
   [service-principal.md](service-principal.md).
6. Spoofed identity, altered hash, unauthorized approval, expiry, replay,
   target drift, and missing audit storage tests fail without mutation.
7. A valid low-risk test action executes once and produces a complete plan,
   approval, execution, and verification trail.

Then set `BUNDLE_VAR_actions_enabled=true` through a reviewed deployment.
Turning on this flag only permits approval/executor submission; it does not
bypass any durable checks.

## Curated schedules and compute

The Platform Console declares `started: true`. Its dedicated 2X-Small
serverless SQL warehouse starts on first query and auto-stops after five idle
minutes.

All schedule definitions default to `PAUSED`. Dev and UAT keep that default.
Production target overrides unpause exactly:

- `azure_cost_pull` daily at 06:30 UTC;
- `cost_usage_report` daily at 07:00 UTC;
- `security_audit` Monday at 06:00 UTC;
- `governance_check` Monday at 06:30 UTC;
- `platform_digest` Monday at 08:00 UTC.

All other scheduled Jobs stay paused. When one is needed, create an exact
`run-job` plan in Action Center and use the normal approval/executor flow.
Never use Databricks **Run now** for a stateful evidence writer because it
lacks the durable approval attestation.

CI builds and deploys the bundle, runs `schema_migrations`, and synchronizes
estimator prompts. It does not run a lifecycle reconciliation job.

After the first complete week, verify 17 scheduled Job triggers, confirm no new
controller runs, and compare serverless billing with the previous week. A Job
trigger may contain multiple billed tasks, so use system billing rather than
trigger count as the dollar-savings measurement.

### One-time power-controller retirement

Production already has a bundle-bound controller Job. Resource deletion is
unsupported, so retire it without deleting it:

1. Deploy the release that removes CI invocation and app access while the old
   Job definition is still present and unscheduled.
2. Resolve the production bundle summary and verify the exact
   `power_controller` binding and remote Job ID.
3. Run `databricks bundle deployment unbind power_controller -t prod`.
4. Without an intervening deployment from a revision that still declares the
   controller, deploy the release that removes its bundle definition.

The old workspace Job remains inert and unmanaged. Existing runtime-state
tables and historical action/audit rows are retained; retired runtime actions
cannot be approved or executed.

## Assistant model access

The FastAPI backend hosts the LangGraph ReAct agent in-process. The graph uses
the Databricks-hosted endpoint configured by `var.chat_model` (default
`databricks-claude-sonnet-4-5`) as its LLM. The bundle binds that endpoint as
the `chat-model` App resource with `CAN_QUERY` and injects its exact name
through `DBX_PLATFORM_CHAT_ENDPOINT`.

The graph receives the current page context and browser-held conversation. Its
allowlisted tools reuse package checks for SQL-backed operational evidence and
the canonical `platform_findings` repository for privileged scheduled
evidence. It has no executor or target-mutation tool.

If chat returns `agent_unavailable`, verify that the endpoint is `READY`, the
App deployment includes the `chat-model` resource, and the active deployment
contains `DBX_PLATFORM_CHAT_ENDPOINT`. Also verify the App environment
installed the locked `langgraph` and `mlflow-tracing` dependencies.

The App records each agent invocation as a native MLflow `AGENT` span in the
bound `agent-traces` experiment. This uses the lightweight tracing SDK directly
and does not require the optional `langchain` distribution. Trace destination,
start, input/output, and finalization failures are warning-only so an
observability outage cannot make chat unavailable.

The optional MLflow-serving wrapper under `agents/platform_agent/` remains
disabled; the App does not need it. Its deployment helper intentionally exits
because separate model registration/deployment is a governed mutation without
an allowlisted executor action.

## Protected forecast training

`cost-forecast-train` is unscheduled and runs as the action executor identity.
It is exact-bound into the app as a governed manual Job.

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

Create a `stale-clusters` plan in Review & Approve. The executor re-reads state and
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
before enabling it. The Platform Console assistant does not need that action:
its LangGraph runtime is hosted inside the read-only App and queries the
`chat-model` foundation endpoint through an App resource binding with only
`CAN_QUERY`.

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

`llm-cost-rollup` writes workspace-scoped, provider-aware daily AI cost and
hourly serving-usage ledgers. Databricks list-cost coverage includes model
serving, agent evaluation, vector search, online tables, and foundation-model
training. `workload_type` preserves the Databricks billing origin; lowercase
`project`, `app`/`application`, `team`, and `use_case` billing tags are stored
without inferring missing values.

For a Databricks App such as `agent-eval`, assign a serverless usage policy
containing `project=agent-eval` and `app=agent-eval`. Policy changes apply only
to future usage; historical untagged rows stay visibly `unallocated`, and new
billing rows can take up to 24 hours to arrive.

After deploying a ledger schema expansion, run `schema_migrations`, then create
and approve one exact `run-job` action for `[dbx-platform] llm-cost-backfill`.
The unscheduled Job has a fixed 400-day window and accepts no caller-controlled
lookback. The normal scheduled rollup remains fixed at three days.
Interpret labels literally:

- `Azure actual`: Azure billing, including later adjustments;
- `Databricks list`: usage joined to list prices, not an invoice;
- `provider estimate`: AI Gateway/provider estimate, never silently combined
  with actual billed cost.

Azure billed cost is ingested only after Cost Management applies the configured
resource-group allowlist. The Databricks/Azure reconciliation view is a daily
SKU-family bridge, not invoice-line equivalence; it withholds variance when the
Azure billing currency is not USD. Compute, storage, networking, commitments,
credits, and tax lines can remain unmatched by design.

Paid Genie usage is included from `billing_origin_product = 'GENIE'` in the
Databricks list-cost basis. SQL warehouse compute used by Genie remains a
separate Databricks product cost. Use native Databricks Genie budgets for
near-real-time alerts or blocking; Mission Control budgets are analytical,
approval-gated guardrails and do not replace native enforcement.

Do not add currencies without a documented conversion source/rate/time.
Request telemetry allocates billed totals to workloads but does not claim
invoice-accurate per-request cost. Keep an explicit `unallocated/uncovered`
bucket.

Cost/request and token unit economics use only `MODEL_SERVING` financial rows.
Agent-evaluation and other AI product costs remain in total spend but are never
divided by serving request or token counts.

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

### Embedded dashboards in the console

The Learn page embeds each published `[dbx-platform]` dashboard with the
workspace-qualified basic-embedding URL:

`<workspace-host>/embed/dashboardsv3/<dashboard-id>?o=<workspace-id>`

Basic embedding authenticates the viewer with their existing Databricks
workspace browser session. The App's forwarded user access token remains
server-side and is never returned to the frontend or inserted into the iframe.
`resources/dashboards.yml` keeps `embed_credentials: false`, so the viewer
still needs `CAN_VIEW` on the dashboard plus access to its warehouse and
underlying Unity Catalog data.

Before using the iframe, a workspace admin must add the App's domain to the
approved domains for dashboard embedding. The browser must also allow
third-party cookies for the Databricks App and workspace domains; otherwise
the iframe can show a second **Continue** prompt even when the user is already
signed in to the App. Use **Open in workspace** as the fallback because it
opens the same workspace-qualified dashboard outside the iframe.

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
uv run ruff check .
uv run pytest
uv run python -m build --wheel
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

# Identity and least-privilege grant matrix

Mission Control uses separate identities so compromise of one component does
not turn observation or proposal capability into execution capability.

Never reuse the app identity, deployment identity, runtime executor, or action
executor. Do not grant `ALL PRIVILEGES` on a catalog/schema and do not make an
executor a workspace admin as a convenience.

## Identities

| Principal | Purpose | Must not have |
|---|---|---|
| Deployment identity | Asset Bundle deploy and idempotent schema migration | Runtime/action executor credentials; target API permissions unrelated to deployment |
| App service principal | Read summaries and submit an already approved action ID to executor Jobs | UC `MODIFY`; cluster/job/token/policy/app/warehouse mutation APIs |
| Runtime executor | Exact Hibernate/Wake and durable reconciliation | Non-runtime remediation, UC DDL, shared/unrelated resource access |
| Action executor | Allowlisted remediation, protected manual Job launch, budget write | App/runtime control, UC DDL, resource deletion |
| Scheduled report identity | Read sources; append findings/cost/telemetry | Resource/configuration mutation APIs |
| Human viewer | Read masked Mission Control data | Action-table writes |
| Human operator/proposer | Create plans under forwarded user authorization | Approval unless also in approver group; executor API permissions |
| Human approver | Approve/reject exact plans | Executor service-principal credentials |

The Databricks App authenticates the person with the forwarded user access
token and resolves `/Me`/group membership server-side. Forwarded email alone is
never authorization.

## Workspace/API matrix

| Target | Deployment | App | Runtime executor | Action executor | Reports |
|---|---:|---:|---:|---:|---:|
| Platform Console app | deploy/manage | — | start/stop + status | none | none |
| Dedicated Mission Control warehouse | deploy/configure | `CAN_USE` | `CAN_MANAGE` | `CAN_USE` only if an enabled action queries it | `CAN_USE` |
| Eleven scheduled report Jobs | deploy/manage | `CAN_VIEW` on exact IDs | `CAN_MANAGE` on exact IDs | none | run-as |
| Power controller Job | deploy/manage | `CAN_MANAGE_RUN` | run-as | none | none |
| Action executor Job | deploy/manage | `CAN_MANAGE_RUN` | none | run-as | none |
| Protected forecast-training Job | deploy/manage | `CAN_VIEW` exact ID | none | `CAN_MANAGE_RUN`, run-as | none |
| Schema migration Job | deploy/run | none | none | none | none |
| Eligible clusters | deploy only if bundle-owned | none | none | `CAN_MANAGE` on exact approved scope | read if needed |
| Eligible orphaned Jobs | deploy/manage if bundle-owned | none | none | `CAN_MANAGE` on exact approved scope | read |
| PAT token-management API | none | none | none | workspace-admin-level capability; keep pack disabled unless accepted | read/list only if security pack enabled |
| Cluster policies | deploy managed policy resource | none | none | create/edit only enabled managed scope; never delete | read |

Databricks currently exposes some administrative APIs only through powerful
workspace entitlements. If a narrow grant cannot be expressed, keep that action
pack proposal-only. In particular, do not make the whole action executor a
workspace admin merely to enable PAT revocation.

The runtime executor’s `CAN_MANAGE` on the dedicated warehouse is needed to
inspect all active statements and stop it. It must have no permission on the
shared Starter warehouse.

The protected training Job is injected into the app through the exact bundle
ID (`DBX_PLATFORM_GOVERNED_MANUAL_JOB_IDS`) and is not placed in the Hibernate
inventory.

## Unity Catalog matrix

Use a dedicated `main.dbx_platform` schema (or change catalog/schema
consistently). A metastore administrator should create it once and assign
ownership to a deployment group:

```sql
CREATE SCHEMA IF NOT EXISTS main.dbx_platform;
ALTER SCHEMA main.dbx_platform OWNER TO `dbx-platform-deployers`;
```

Schema ownership is intentionally confined to this application schema. The
deployment identity/group runs `schema_migrations`; no runtime identity gets
`CREATE TABLE`, `CREATE FUNCTION`, `MANAGE`, or ownership.

Grant catalog/schema visibility:

```sql
GRANT USE CATALOG ON CATALOG main TO `dbx-platform-app`;
GRANT USE CATALOG ON CATALOG main TO `dbx-platform-runtime-executor`;
GRANT USE CATALOG ON CATALOG main TO `dbx-platform-action-executor`;
GRANT USE CATALOG ON CATALOG main TO `dbx-platform-reporters`;
GRANT USE CATALOG ON CATALOG main TO `dbx-platform-viewers`;
GRANT USE CATALOG ON CATALOG main TO `dbx-platform-operators`;
GRANT USE CATALOG ON CATALOG main TO `dbx-platform-approvers`;

GRANT USE SCHEMA ON SCHEMA main.dbx_platform TO `dbx-platform-app`;
GRANT USE SCHEMA ON SCHEMA main.dbx_platform TO `dbx-platform-runtime-executor`;
GRANT USE SCHEMA ON SCHEMA main.dbx_platform TO `dbx-platform-action-executor`;
GRANT USE SCHEMA ON SCHEMA main.dbx_platform TO `dbx-platform-reporters`;
GRANT USE SCHEMA ON SCHEMA main.dbx_platform TO `dbx-platform-viewers`;
GRANT USE SCHEMA ON SCHEMA main.dbx_platform TO `dbx-platform-operators`;
GRANT USE SCHEMA ON SCHEMA main.dbx_platform TO `dbx-platform-approvers`;
```

Replace group-style names with exact service-principal application IDs where
your workspace does not use wrapping groups.

### Read-only app and viewers

Grant `SELECT` only:

```sql
GRANT SELECT ON TABLE main.dbx_platform.platform_findings TO `dbx-platform-app`;
GRANT SELECT ON TABLE main.dbx_platform.platform_digest TO `dbx-platform-app`;
GRANT SELECT ON TABLE main.dbx_platform.action_requests TO `dbx-platform-app`;
GRANT SELECT ON TABLE main.dbx_platform.action_approvals TO `dbx-platform-app`;
GRANT SELECT ON TABLE main.dbx_platform.action_events TO `dbx-platform-app`;
GRANT SELECT ON TABLE main.dbx_platform.managed_resources TO `dbx-platform-app`;
GRANT SELECT ON TABLE main.dbx_platform.platform_runtime_state TO `dbx-platform-app`;
GRANT SELECT ON TABLE main.dbx_platform.llm_usage_hourly TO `dbx-platform-app`;
GRANT SELECT ON TABLE main.dbx_platform.llm_cost_daily TO `dbx-platform-app`;
GRANT SELECT ON TABLE main.dbx_platform.llm_budgets TO `dbx-platform-app`;
```

Apply the same table-level `SELECT` set to `dbx-platform-viewers`,
`dbx-platform-operators`, and `dbx-platform-approvers`. Identity masking is
still enforced by the application role; UC grants alone do not replace it.

The app service principal and human groups must not receive `MODIFY` on action
or budget tables. Plans and decisions use the verified person’s forwarded
authorization to call narrow `SQL SECURITY DEFINER` procedures. The procedures
re-check account-group membership and record `session_user()`; direct table
edits remain impossible.

### Operators and approvers

The deployment migration creates and grants these write-broker procedures:

```sql
GRANT EXECUTE ON PROCEDURE main.dbx_platform.cp_create_action
  TO `dbx-platform-operators`;
GRANT EXECUTE ON PROCEDURE main.dbx_platform.cp_transition_action
  TO `dbx-platform-operators`;
GRANT EXECUTE ON PROCEDURE main.dbx_platform.cp_append_event
  TO `dbx-platform-operators`;

GRANT EXECUTE ON PROCEDURE main.dbx_platform.cp_create_action
  TO `dbx-platform-approvers`;
GRANT EXECUTE ON PROCEDURE main.dbx_platform.cp_transition_action
  TO `dbx-platform-approvers`;
GRANT EXECUTE ON PROCEDURE main.dbx_platform.cp_decide_action
  TO `dbx-platform-approvers`;
GRANT EXECUTE ON PROCEDURE main.dbx_platform.cp_append_event
  TO `dbx-platform-approvers`;
```

`cp_decide_action` writes the status, one approval, and its audit event inside
one atomic block. It accepts only `APPROVED` or `REJECTED`, requires the exact
current status and plan hash, and verifies the connected user against
`dbx-platform-approvers`. The other procedures similarly restrict proposal,
stale/expired transition, and non-status event types. Do not replace these
grants with human table `MODIFY`.

Atomic multi-table decisions require Unity Catalog managed Delta tables with
Catalog commits enabled and supported current serverless/SQL compute. Keep
`actions_enabled=false` until that prerequisite and a complete negative test
cycle have been verified.

### Runtime executor

```sql
GRANT SELECT, MODIFY ON TABLE main.dbx_platform.action_requests
  TO `dbx-platform-runtime-executor`;
GRANT SELECT ON TABLE main.dbx_platform.action_approvals
  TO `dbx-platform-runtime-executor`;
GRANT SELECT, MODIFY ON TABLE main.dbx_platform.action_events
  TO `dbx-platform-runtime-executor`;
GRANT SELECT, MODIFY ON TABLE main.dbx_platform.managed_resources
  TO `dbx-platform-runtime-executor`;
GRANT SELECT, MODIFY ON TABLE main.dbx_platform.platform_runtime_state
  TO `dbx-platform-runtime-executor`;
```

The runtime executor preflights both lifecycle update and event append. Missing
storage or write permission stops it before observing/mutating managed targets.

### Action executor

Base ledger grants:

```sql
GRANT SELECT, MODIFY ON TABLE main.dbx_platform.action_requests
  TO `dbx-platform-action-executor`;
GRANT SELECT ON TABLE main.dbx_platform.action_approvals
  TO `dbx-platform-action-executor`;
GRANT SELECT, MODIFY ON TABLE main.dbx_platform.action_events
  TO `dbx-platform-action-executor`;
```

Enable only required packs:

```sql
-- configure-budget
GRANT SELECT, MODIFY ON TABLE main.dbx_platform.llm_budgets
  TO `dbx-platform-action-executor`;

-- protected forecast training validates the durable launch ledger
GRANT SELECT ON TABLE main.dbx_platform.action_requests
  TO `dbx-platform-action-executor`;
GRANT SELECT ON TABLE main.dbx_platform.action_approvals
  TO `dbx-platform-action-executor`;
GRANT SELECT ON TABLE main.dbx_platform.action_events
  TO `dbx-platform-action-executor`;
```

No executor receives schema ownership or DDL.

### Scheduled report identity

Scheduled writers need only their destination tables. Assign `SELECT, MODIFY`
per pack, for example:

```sql
GRANT SELECT, MODIFY ON TABLE main.dbx_platform.platform_findings
  TO `dbx-platform-reporters`;
GRANT SELECT, MODIFY ON TABLE main.dbx_platform.platform_digest
  TO `dbx-platform-reporters`;
GRANT SELECT, MODIFY ON TABLE main.dbx_platform.llm_usage_hourly
  TO `dbx-platform-reporters`;
GRANT SELECT, MODIFY ON TABLE main.dbx_platform.llm_cost_daily
  TO `dbx-platform-reporters`;
```

Azure cost/forecast jobs additionally need only their own `azure_costs`,
`azure_cost_details`, `cost_features`, and `cost_forecasts` tables.

## System-table grants

Grant the scheduled report identity only the schemas its enabled packs read:

```sql
GRANT USE SCHEMA, SELECT ON SCHEMA system.billing
  TO `dbx-platform-reporters`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.access
  TO `dbx-platform-reporters`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.lakeflow
  TO `dbx-platform-reporters`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.compute
  TO `dbx-platform-reporters`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.query
  TO `dbx-platform-reporters`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.serving
  TO `dbx-platform-reporters`;
```

AI Gateway preview schemas/tables should be granted only after feature
detection shows they exist. Their absence is a visible coverage state, not a
reason to broaden privileges.

The app needs system-table access only for live views that cannot be served
from cached ledgers. Prefer cached summaries.

## Azure permissions

The identity behind the UC service credential used by `azure-cost-pull`
receives Azure `Cost Management Reader` at the smallest supported billing
scope. It does not need Contributor and cannot create Azure budgets/exports.

Azure budget changes are not executed through ARM in v1; Mission Control’s LLM
budget action writes only its own governed `llm_budgets` table.

## Databricks App authorization

The App resource binds:

- dedicated warehouse as `CAN_USE`;
- power controller as `CAN_MANAGE_RUN`;
- action executor as `CAN_MANAGE_RUN`.

Those bindings let the app submit only executor Job parameters. Its exact
protected-forecast ID binding requires `CAN_VIEW` so bundle reconciliation
cannot overwrite that Job ACL; only the action executor has `CAN_MANAGE_RUN`.

Databricks automatically grants the two default identity scopes when user
authorization is enabled:

- `iam.current-user:read`;
- `iam.access-control:read`.

Do not submit those defaults explicitly in the App resource: workspaces can
reject them as invalid explicit scopes. The only additional scope declared by
dbx-platform is `sql`.

Do not add broad workspace-management scopes to make authorization easier.

## CI authentication

The current pipeline uses keyless GitHub OIDC:

`GitHub OIDC → Azure login → Databricks unified auth`

Workspace-touching workflows must use the protected `production` environment
and `id-token: write`; PR workflows must never receive workspace credentials.

Required production variables:

- `DBX_PLATFORM_RUNTIME_EXECUTOR_SP`
- `DBX_PLATFORM_ACTION_EXECUTOR_SP`

The workflow fails if either is missing. There is no shared warehouse ID
secret because the bundle owns the dedicated warehouse.

If using Databricks OAuth M2M/client-secret auth instead, store only the
deployment identity’s client ID/secret. Executor credentials are configured as
Databricks Job `run_as` identities, not exposed to the app or GitHub steps.

## Controlled grant procedure

1. Create/register all identities and groups.
2. Pre-create `main.dbx_platform` and assign ownership only to the deployment
   group.
3. Apply base read/ledger grants.
4. Deploy with actions disabled and run migrations.
5. Validate reports and negative safety tests.
6. Grant one action pack at a time to the action executor.
7. Record every grant and exact target in the deployment change.
8. Remove unused grants after disabling a pack.

Use `SHOW GRANTS` to compare actual versus this matrix. Any app `MODIFY`, any
executor DDL, any runtime permission on shared/unrelated resources, or any
blanket workspace-admin grant is a release blocker unless explicitly
documented as an accepted constraint for a disabled-by-default action pack.

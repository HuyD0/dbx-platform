# AppKit analytics queries

This directory is the AppKit analytics query boundary for Platform Console.
React components refer to these files by query key and never embed SQL.

Every query currently uses the `.obo.sql` suffix so AppKit executes it with
the requesting user's identity and keeps its cache user-scoped. The
`:workspaceId` parameter is injected by AppKit; all other parameters are
declared with `-- @param` annotations and must be bound by the caller.

| Query key | Parameters | Purpose |
|---|---|---|
| `ai_gateway_latency` | `lookbackDays` | Successful-invocation p50/p95/p99 latency and p95 time to first byte |
| `ai_gateway_throttling` | `lookbackDays` | Final HTTP 429 counts and rates |
| `ai_gateway_token_throughput` | `lookbackDays` | Derived post-first-byte output-token speed |
| `ai_gateway_agent_workflows` | `lookbackDays`, `resultLimit` | Multi-invocation request-chain candidates |
| `ai_gateway_tag_attribution` | `lookbackDays` | Arbitrary request-tag key/value attribution |
| `data_quality_alerts` | `catalogName`, `schemaName`, `lookbackDays`, `resultLimit` | Latest freshness and completeness incidents |

## Access and freshness constraints

- `system.ai_gateway.usage` is a Beta, regional system table. Databricks
  currently limits it to account-admin viewers, so these OBO queries fail
  closed for other users. A later phase can materialize a curated governed
  aggregate if the dashboard needs broader access.
- `system.data_quality_monitoring.table_results` contains metastore-wide data,
  including samples and downstream usage information. Only account admins
  receive access by default; grant access only to authorized operators.
- System tables update throughout the day. These queries provide operational
  telemetry and scan results, not a real-time alerting channel.
- Request tags are caller-supplied attribution labels. Treat them as trusted
  cost/governance dimensions only when the gateway or client policy enforces
  their values.

The existing FastAPI runtime remains active during this scaffold phase. When
the Node/AppKit server is introduced, configure the analytics plugin with
`autoStartWarehouse: false`; dashboard reads must not start billable compute
outside the repository's approval-gated runtime workflow.

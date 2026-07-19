-- @param lookbackDays INT
-- @param resultLimit INT
-- Multiple invocations under one request can represent an agent chain,
-- guardrail checks, or a multi-turn call. This is a workflow-candidate
-- heuristic, not a definitive agent classifier.
WITH request_chains AS (
  SELECT
    account_id,
    workspace_id,
    request_id,
    MIN(event_time) AS first_invocation_at,
    MAX(
      TIMESTAMPADD(
        MILLISECOND,
        COALESCE(latency_ms, 0),
        event_time
      )
    ) AS last_invocation_finished_at,
    COUNT(DISTINCT invocation_id) AS invocation_count,
    ARRAY_SORT(COLLECT_SET(endpoint_name)) AS endpoints,
    ARRAY_SORT(COLLECT_SET(invocation_metadata.source)) AS invocation_sources,
    ARRAY_SORT(COLLECT_SET(status_code)) AS status_codes,
    COALESCE(
      MAX(request_tags['team']),
      MAX(endpoint_tags['team']),
      'unattributed'
    ) AS attributed_team,
    COALESCE(
      MAX(request_tags['agent']),
      MAX(request_tags['agent_name']),
      MAX(request_tags['application']),
      MAX(request_tags['app']),
      'unattributed'
    ) AS attributed_agent,
    COALESCE(MAX(request_tags['project']), 'unattributed') AS attributed_project,
    SUM(COALESCE(input_tokens, 0)) AS input_tokens,
    SUM(COALESCE(output_tokens, 0)) AS output_tokens,
    SUM(CASE WHEN status_code = 429 THEN 1 ELSE 0 END) AS throttled_invocations,
    SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) AS failed_invocations
  FROM system.ai_gateway.usage
  WHERE workspace_id = :workspaceId
    AND event_time >= DATE_SUB(CURRENT_DATE(), :lookbackDays)
    AND request_id IS NOT NULL
    AND invocation_id IS NOT NULL
  GROUP BY account_id, workspace_id, request_id
  HAVING COUNT(DISTINCT invocation_id) > 1
)
SELECT
  account_id,
  workspace_id,
  request_id,
  first_invocation_at,
  last_invocation_finished_at,
  TIMESTAMPDIFF(
    MILLISECOND,
    first_invocation_at,
    last_invocation_finished_at
  ) AS observed_chain_span_ms,
  invocation_count,
  endpoints,
  invocation_sources,
  status_codes,
  attributed_team,
  attributed_agent,
  attributed_project,
  input_tokens,
  output_tokens,
  throttled_invocations,
  failed_invocations
FROM request_chains
ORDER BY last_invocation_finished_at DESC
LIMIT :resultLimit

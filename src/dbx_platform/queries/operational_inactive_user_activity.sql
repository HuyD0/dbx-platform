-- Last observed audit activity for users in the current workspace only.
SELECT
  user_identity.email AS email,
  MAX(event_time) AS last_seen,
  COUNT(*) AS events
FROM system.access.audit
WHERE workspace_id = :workspace_id
  AND event_time >= TIMESTAMPADD(DAY, -:days, CURRENT_TIMESTAMP())
  AND user_identity.email IS NOT NULL
  AND user_identity.email NOT IN ('System-User', 'unknown')
GROUP BY user_identity.email

-- Last observed activity per user over the last :days days.
-- The caller anti-joins this against the SCIM user list: any workspace user
-- absent from this result set had zero audited activity in the window.
-- Source: system.access.audit
SELECT
  user_identity.email      AS email,
  MAX(event_time)          AS last_seen,
  COUNT(*)                 AS events
FROM system.access.audit
WHERE event_time >= TIMESTAMPADD(DAY, -:days, CURRENT_TIMESTAMP())
  AND user_identity.email IS NOT NULL
  AND user_identity.email NOT IN ('System-User', 'unknown')
GROUP BY user_identity.email

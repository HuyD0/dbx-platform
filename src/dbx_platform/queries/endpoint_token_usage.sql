-- Token usage per serving endpoint and requester over the last :days days.
-- Sources: system.serving.endpoint_usage x system.serving.served_entities
-- (public preview — callers degrade gracefully when the schema is absent).
SELECT
  COALESCE(se.endpoint_name, eu.served_entity_id)  AS endpoint_name,
  eu.requester,
  COUNT(*)                                         AS requests,
  SUM(eu.input_token_count)                        AS input_tokens,
  SUM(eu.output_token_count)                       AS output_tokens
FROM system.serving.endpoint_usage eu
LEFT JOIN system.serving.served_entities se
  ON eu.served_entity_id = se.served_entity_id
WHERE DATE(eu.request_time) >= DATE_SUB(CURRENT_DATE(), :days)
GROUP BY COALESCE(se.endpoint_name, eu.served_entity_id), eu.requester
ORDER BY input_tokens + output_tokens DESC

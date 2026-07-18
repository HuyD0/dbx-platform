-- Serving health from the canonical persisted hourly usage ledger.
-- p95_latency_ms is already an aggregate, so v1 compares request-weighted
-- hourly p95 values rather than claiming to reconstruct request-level p95.
WITH scoped AS (
  SELECT *
  FROM __LLM_USAGE_HOURLY__
  WHERE workspace_id = :workspace_id
    AND environment = :environment
    AND usage_hour >= DATE_SUB(CURRENT_DATE(), :window_days)
)
SELECT
  endpoint,
  provider,
  model,
  source,
  SUM(
    CASE
      WHEN usage_hour >= DATE_SUB(CURRENT_DATE(), :recent_days)
        THEN requests
      ELSE 0
    END
  ) AS recent_requests,
  SUM(
    CASE
      WHEN usage_hour < DATE_SUB(CURRENT_DATE(), :recent_days)
        THEN requests
      ELSE 0
    END
  ) AS baseline_requests,
  SUM(
    CASE
      WHEN usage_hour >= DATE_SUB(CURRENT_DATE(), :recent_days)
        AND p95_latency_ms IS NOT NULL
        THEN p95_latency_ms * requests
      ELSE 0
    END
  ) / NULLIF(
    SUM(
      CASE
        WHEN usage_hour >= DATE_SUB(CURRENT_DATE(), :recent_days)
          AND p95_latency_ms IS NOT NULL
          THEN requests
        ELSE 0
      END
    ),
    0
  ) AS recent_weighted_p95_latency_ms,
  SUM(
    CASE
      WHEN usage_hour < DATE_SUB(CURRENT_DATE(), :recent_days)
        AND p95_latency_ms IS NOT NULL
        THEN p95_latency_ms * requests
      ELSE 0
    END
  ) / NULLIF(
    SUM(
      CASE
        WHEN usage_hour < DATE_SUB(CURRENT_DATE(), :recent_days)
          AND p95_latency_ms IS NOT NULL
          THEN requests
        ELSE 0
      END
    ),
    0
  ) AS baseline_weighted_p95_latency_ms,
  COUNT_IF(
    usage_hour >= DATE_SUB(CURRENT_DATE(), :recent_days)
      AND p95_latency_ms IS NOT NULL
  ) AS recent_latency_metric_rows,
  SUM(
    CASE
      WHEN usage_hour >= DATE_SUB(CURRENT_DATE(), :recent_days)
        THEN errors
    END
  ) AS recent_errors,
  COUNT_IF(
    usage_hour >= DATE_SUB(CURRENT_DATE(), :recent_days)
      AND errors IS NOT NULL
  ) AS recent_error_metric_rows,
  MAX(usage_hour) AS evidence_freshness_at,
  TIMESTAMPDIFF(HOUR, MAX(usage_hour), CURRENT_TIMESTAMP()) AS freshness_age_hours
FROM scoped
GROUP BY endpoint, provider, model, source
ORDER BY recent_requests DESC
LIMIT :limit

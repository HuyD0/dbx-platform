"""Security-definer write broker for human Mission Control decisions.

The Databricks App service principal and human groups receive no ``MODIFY`` on
the action ledger. Only the App service principal receives ``EXECUTE`` on these
narrowly scoped Unity Catalog procedures. The App re-checks the forwarded
user's live account-group membership and passes that verified identity as the
actor. Human groups deliberately cannot call the procedures directly and
substitute another actor identity.
"""

from __future__ import annotations

import re

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PRINCIPAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}$")


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"Unsafe Unity Catalog identifier: {value!r}")
    return value


def _principal(value: str) -> str:
    if not _PRINCIPAL.fullmatch(value):
        raise ValueError(f"Unsafe Unity Catalog principal: {value!r}")
    return value


def procedure_statements(
    catalog: str,
    schema: str,
    *,
    app_service_principal: str,
    operator_group: str,
    approver_group: str,
) -> list[tuple[str, str]]:
    """Return idempotent SQL SECURITY DEFINER procedure migrations."""

    catalog = _identifier(catalog)
    schema = _identifier(schema)
    app_service_principal = _principal(app_service_principal)
    operator_group = _principal(operator_group)
    approver_group = _principal(approver_group)
    fq = f"`{catalog}`.`{schema}`"
    # Databricks does not allow is_account_group_member() or session_user()
    # inside an atomic stored-procedure transaction. Keep the procedures atomic
    # and make the App the sole caller. Its request boundary verifies the
    # forwarded user and live group membership before supplying actor fields.
    allowed_actions = (
        "'stale-clusters', 'orphaned-jobs', 'token-revoke', 'policy-sync', "
        "'run-job', 'configure-budget'"
    )

    create_action = f"""
CREATE OR REPLACE PROCEDURE {fq}.`cp_create_action`(
  IN p_workspace_id STRING,
  IN p_environment STRING,
  IN p_action_id STRING,
  IN p_action_type STRING,
  IN p_plan_json STRING,
  IN p_plan_hash STRING,
  IN p_confirm_phrase STRING,
  IN p_risk STRING,
  IN p_proposer_id STRING,
  IN p_proposer_email STRING,
  IN p_created_at STRING,
  IN p_expires_at STRING,
  IN p_updated_at STRING,
  IN p_idempotency_key STRING
)
LANGUAGE SQL
SQL SECURITY DEFINER
MODIFIES SQL DATA
AS BEGIN ATOMIC
  IF p_proposer_id IS NULL OR p_proposer_id = ''
     OR p_proposer_email IS NULL OR p_proposer_email = '' THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The verified proposer identity is required';
  END IF;
  IF p_action_type NOT IN ({allowed_actions}) THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The proposed action type is not allowlisted';
  END IF;
  IF (p_action_type = 'token-revoke' AND p_risk <> 'HIGH')
     OR (p_action_type = 'run-job' AND p_risk NOT IN ('LOW', 'MEDIUM'))
     OR (p_action_type NOT IN ('token-revoke', 'run-job')
         AND p_risk <> 'MEDIUM') THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The action risk does not match the allowlisted policy';
  END IF;
  IF CAST(p_expires_at AS TIMESTAMP)
       <> CAST(p_created_at AS TIMESTAMP) + INTERVAL 15 MINUTES
     OR CAST(p_updated_at AS TIMESTAMP) <> CAST(p_created_at AS TIMESTAMP)
     OR CAST(p_created_at AS TIMESTAMP)
       < current_timestamp() - INTERVAL 5 MINUTES
     OR CAST(p_created_at AS TIMESTAMP)
       > current_timestamp() + INTERVAL 1 MINUTE THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The action timestamps or 15-minute TTL are invalid';
  END IF;
  IF p_confirm_phrase <> concat(
       'apply ', p_action_type, ' ',
       CAST(json_array_length(get_json_object(p_plan_json, '$.targets')) AS STRING)
     ) THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The immutable confirmation marker is not canonical';
  END IF;
  IF sha2(p_plan_json, 256) <> lower(p_plan_hash)
     OR get_json_object(p_plan_json, '$.schema_version') <> '1'
     OR get_json_object(p_plan_json, '$.action_id') <> p_action_id
     OR get_json_object(p_plan_json, '$.action_type') <> p_action_type
     OR get_json_object(p_plan_json, '$.workspace_id') <> p_workspace_id
     OR get_json_object(p_plan_json, '$.environment') <> p_environment
     OR get_json_object(p_plan_json, '$.risk') <> p_risk
     OR get_json_object(p_plan_json, '$.proposer_id') <> p_proposer_id
     OR lower(get_json_object(p_plan_json, '$.proposer_email'))
       <> lower(p_proposer_email)
     OR get_json_object(p_plan_json, '$.created_at') <> p_created_at
     OR get_json_object(p_plan_json, '$.expires_at') <> p_expires_at
     OR get_json_object(p_plan_json, '$.confirm_phrase') <> p_confirm_phrase
     OR get_json_object(p_plan_json, '$.idempotency_key') <> p_idempotency_key THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The immutable action document is inconsistent';
  END IF;
  IF EXISTS (
    SELECT 1 FROM {fq}.`action_requests`
    WHERE workspace_id = p_workspace_id
      AND environment = p_environment
      AND action_id = p_action_id
  ) THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The action ID already exists';
  END IF;
  INSERT INTO {fq}.`action_requests` (
    workspace_id, environment, action_id, action_type, status, plan_json,
    plan_hash, confirm_phrase, risk, proposer_id, proposer_email, created_at,
    expires_at, updated_at, idempotency_key, terminal_reason
  ) VALUES (
    p_workspace_id, p_environment, p_action_id, p_action_type,
    'AWAITING_APPROVAL', p_plan_json, p_plan_hash, p_confirm_phrase, p_risk,
    p_proposer_id, p_proposer_email, CAST(p_created_at AS TIMESTAMP),
    CAST(p_expires_at AS TIMESTAMP), CAST(p_updated_at AS TIMESTAMP),
    p_idempotency_key, NULL
  );
END
""".strip()

    transition_action = f"""
CREATE OR REPLACE PROCEDURE {fq}.`cp_transition_action`(
  IN p_workspace_id STRING,
  IN p_environment STRING,
  IN p_action_id STRING,
  IN p_expected_statuses STRING,
  IN p_target_status STRING,
  IN p_reason STRING,
  IN p_event_id STRING,
  IN p_actor_id STRING,
  IN p_details_json STRING,
  IN p_event_at STRING
)
LANGUAGE SQL
SQL SECURITY DEFINER
MODIFIES SQL DATA
AS BEGIN ATOMIC
  DECLARE v_from_status STRING;
  IF p_actor_id IS NULL OR p_actor_id = '' THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The verified transition actor is required';
  END IF;
  IF p_target_status NOT IN ('STALE', 'EXPIRED') THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'Human transition target is not allowlisted';
  END IF;
  SET v_from_status = (
    SELECT max(status) FROM {fq}.`action_requests`
    WHERE workspace_id = p_workspace_id
      AND environment = p_environment
      AND action_id = p_action_id
  );
  IF v_from_status IS NULL
     OR NOT array_contains(split(p_expected_statuses, ','), v_from_status) THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The action status changed concurrently';
  END IF;
  UPDATE {fq}.`action_requests`
  SET status = p_target_status,
      updated_at = CAST(p_event_at AS TIMESTAMP),
      terminal_reason = nullif(p_reason, '')
  WHERE workspace_id = p_workspace_id
    AND environment = p_environment
    AND action_id = p_action_id
    AND status = v_from_status;
  INSERT INTO {fq}.`action_events` (
    workspace_id, environment, event_id, action_id, event_type, from_status,
    to_status, actor_id, details_json, event_ts
  ) VALUES (
    p_workspace_id, p_environment, p_event_id, p_action_id,
    concat('STATUS_', p_target_status), v_from_status, p_target_status,
    p_actor_id, p_details_json, CAST(p_event_at AS TIMESTAMP)
  );
END
""".strip()

    decide_action = f"""
CREATE OR REPLACE PROCEDURE {fq}.`cp_decide_action`(
  IN p_workspace_id STRING,
  IN p_environment STRING,
  IN p_action_id STRING,
  IN p_expected_status STRING,
  IN p_target_status STRING,
  IN p_plan_hash STRING,
  IN p_approval_id STRING,
  IN p_decision STRING,
  IN p_approver_id STRING,
  IN p_approver_email STRING,
  IN p_confirmation STRING,
  IN p_reason STRING,
  IN p_event_id STRING,
  IN p_details_json STRING,
  IN p_decided_at STRING
)
LANGUAGE SQL
SQL SECURITY DEFINER
MODIFIES SQL DATA
AS BEGIN ATOMIC
  DECLARE v_status STRING;
  DECLARE v_expires_at TIMESTAMP;
  IF p_approver_id IS NULL OR p_approver_id = ''
     OR p_approver_email IS NULL OR p_approver_email = '' THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The verified approver identity is required';
  END IF;
  IF NOT (
    (p_target_status = 'APPROVED' AND p_decision = 'APPROVED'
      AND p_expected_status = 'AWAITING_APPROVAL')
    OR
    (p_target_status = 'REJECTED' AND p_decision = 'REJECTED'
      AND p_expected_status IN ('AWAITING_APPROVAL', 'APPROVED'))
  ) THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The approval decision transition is invalid';
  END IF;
  SET v_status = (
    SELECT max(status) FROM {fq}.`action_requests`
    WHERE workspace_id = p_workspace_id
      AND environment = p_environment
      AND action_id = p_action_id
      AND plan_hash = p_plan_hash
  );
  SET v_expires_at = (
    SELECT max(expires_at) FROM {fq}.`action_requests`
    WHERE workspace_id = p_workspace_id
      AND environment = p_environment
      AND action_id = p_action_id
      AND plan_hash = p_plan_hash
  );
  IF v_status IS NULL OR v_status <> p_expected_status THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The approved action or plan hash changed concurrently';
  END IF;
  IF CAST(p_decided_at AS TIMESTAMP)
       < current_timestamp() - INTERVAL 5 MINUTES
     OR CAST(p_decided_at AS TIMESTAMP)
       > current_timestamp() + INTERVAL 1 MINUTE THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The approval timestamp is invalid';
  END IF;
  IF p_target_status = 'APPROVED'
     AND (current_timestamp() >= v_expires_at
          OR CAST(p_decided_at AS TIMESTAMP) >= v_expires_at) THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The action plan has expired';
  END IF;
  IF EXISTS (
    SELECT 1 FROM {fq}.`action_approvals`
    WHERE workspace_id = p_workspace_id
      AND environment = p_environment
      AND action_id = p_action_id
      AND plan_hash = p_plan_hash
  ) THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'A decision already exists for this exact plan';
  END IF;
  UPDATE {fq}.`action_requests`
  SET status = p_target_status,
      updated_at = CAST(p_decided_at AS TIMESTAMP),
      terminal_reason = nullif(p_reason, '')
  WHERE workspace_id = p_workspace_id
    AND environment = p_environment
    AND action_id = p_action_id
    AND plan_hash = p_plan_hash
    AND status = p_expected_status;
  INSERT INTO {fq}.`action_approvals` (
    workspace_id, environment, approval_id, action_id, plan_hash, decision,
    approver_id, approver_email, approver_role, confirmation, decided_at
  ) VALUES (
    p_workspace_id, p_environment, p_approval_id, p_action_id, p_plan_hash,
    p_decision, p_approver_id, p_approver_email, 'approver',
    nullif(p_confirmation, ''), CAST(p_decided_at AS TIMESTAMP)
  );
  INSERT INTO {fq}.`action_events` (
    workspace_id, environment, event_id, action_id, event_type, from_status,
    to_status, actor_id, details_json, event_ts
  ) VALUES (
    p_workspace_id, p_environment, p_event_id, p_action_id,
    concat('STATUS_', p_target_status), p_expected_status, p_target_status,
    p_approver_id, p_details_json, CAST(p_decided_at AS TIMESTAMP)
  );
END
""".strip()

    append_event = f"""
CREATE OR REPLACE PROCEDURE {fq}.`cp_append_event`(
  IN p_workspace_id STRING,
  IN p_environment STRING,
  IN p_action_id STRING,
  IN p_event_id STRING,
  IN p_event_type STRING,
  IN p_from_status STRING,
  IN p_to_status STRING,
  IN p_actor_id STRING,
  IN p_details_json STRING,
  IN p_event_at STRING
)
LANGUAGE SQL
SQL SECURITY DEFINER
MODIFIES SQL DATA
AS BEGIN ATOMIC
  IF p_actor_id IS NULL OR p_actor_id = '' THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The verified event actor is required';
  END IF;
  IF p_event_type NOT IN (
    'PLAN_CREATED',
    'PLAN_REQUESTED_FROM_APP',
    'EXECUTION_SUBMITTED',
    'EXECUTION_SUBMISSION_FAILED'
  ) THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The human audit event type is not allowlisted';
  END IF;
  IF (p_event_type = 'PLAN_CREATED'
      AND (nullif(p_from_status, '') IS NOT NULL
           OR p_to_status <> 'AWAITING_APPROVAL'))
     OR (p_event_type <> 'PLAN_CREATED'
         AND (nullif(p_from_status, '') IS NOT NULL
              OR nullif(p_to_status, '') IS NOT NULL)) THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The audit event status fields are invalid';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM {fq}.`action_requests`
    WHERE workspace_id = p_workspace_id
      AND environment = p_environment
      AND action_id = p_action_id
  ) THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The audit event action is out of scope';
  END IF;
  INSERT INTO {fq}.`action_events` (
    workspace_id, environment, event_id, action_id, event_type, from_status,
    to_status, actor_id, details_json, event_ts
  ) VALUES (
    p_workspace_id, p_environment, p_event_id, p_action_id, p_event_type,
    nullif(p_from_status, ''), nullif(p_to_status, ''), p_actor_id,
    p_details_json, CAST(p_event_at AS TIMESTAMP)
  );
END
""".strip()

    procedures = {
        "cp_create_action": create_action,
        "cp_transition_action": transition_action,
        "cp_decide_action": decide_action,
        "cp_append_event": append_event,
    }
    statements = [
        (f"procedure {catalog}.{schema}.{name}", sql)
        for name, sql in procedures.items()
    ]
    for name in procedures:
        # Remove grants from the earlier group-authorized implementation. This
        # is intentionally idempotent and closes direct SQL identity spoofing.
        for group in (operator_group, approver_group):
            statements.append(
                (
                    f"revoke {group} execute on {name}",
                    f"REVOKE EXECUTE ON PROCEDURE {fq}.`{name}` FROM `{group}`",
                )
            )
        statements.append(
            (
                f"grant {app_service_principal} execute on {name}",
                f"GRANT EXECUTE ON PROCEDURE {fq}.`{name}` "
                f"TO `{app_service_principal}`",
            )
        )
    return statements


def estimate_procedure_statements(
    catalog: str,
    schema: str,
    *,
    app_service_principal: str,
) -> list[tuple[str, str]]:
    """Security-definer append broker for the saved-estimate library.

    Saving an estimate is telemetry append (no target mutation, no approval
    flow) — the same trust shape as a proposer creating an action request:
    the App verifies the forwarded user's role at the request boundary and
    passes the verified identity as ``p_created_by``. Unlike the action
    procedures, the grant here is NOT gated on ``actions_enabled``; the
    library must work in proposal-only deployments too.
    """

    catalog = _identifier(catalog)
    schema = _identifier(schema)
    app_service_principal = _principal(app_service_principal)
    fq = f"`{catalog}`.`{schema}`"

    record_estimate = f"""
CREATE OR REPLACE PROCEDURE {fq}.`cp_record_estimate`(
  IN p_workspace_id STRING,
  IN p_environment STRING,
  IN p_estimate_id STRING,
  IN p_created_by STRING,
  IN p_title STRING,
  IN p_pattern STRING,
  IN p_monthly_requests STRING,
  IN p_corpus_gb STRING,
  IN p_requirements_json STRING,
  IN p_requirements_hash STRING,
  IN p_engine_version STRING,
  IN p_rate_card_version STRING,
  IN p_snapshot_date STRING,
  IN p_rigor_pct STRING,
  IN p_results_json STRING
)
LANGUAGE SQL
SQL SECURITY DEFINER
MODIFIES SQL DATA
AS BEGIN ATOMIC
  IF p_created_by IS NULL OR p_created_by = '' THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The verified creator identity is required';
  END IF;
  IF p_workspace_id IS NULL OR p_workspace_id = ''
     OR p_environment IS NULL OR p_environment = ''
     OR p_estimate_id IS NULL OR p_estimate_id = '' THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The estimate scope and identifier are required';
  END IF;
  IF p_requirements_hash NOT RLIKE '^[0-9a-f]{{64}}$' THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The requirements hash is not a canonical digest';
  END IF;
  IF p_requirements_json IS NULL OR p_requirements_json = ''
     OR get_json_object(p_requirements_json, '$.pattern') <> p_pattern
     OR p_results_json IS NULL OR p_results_json = '' THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The estimate document is inconsistent';
  END IF;
  IF CAST(p_rigor_pct AS INT) IS NULL
     OR CAST(p_rigor_pct AS INT) < 0 OR CAST(p_rigor_pct AS INT) > 100
     OR CAST(p_monthly_requests AS BIGINT) IS NULL
     OR CAST(p_monthly_requests AS BIGINT) < 1 THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The estimate sizing values are out of bounds';
  END IF;
  IF p_title IS NULL OR p_title = '' OR length(p_title) > 200 THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'A title of at most 200 characters is required';
  END IF;
  IF EXISTS (
    SELECT 1 FROM {fq}.`estimator_estimates`
    WHERE workspace_id = p_workspace_id
      AND environment = p_environment
      AND estimate_id = p_estimate_id
  ) THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The estimate ID already exists';
  END IF;
  INSERT INTO {fq}.`estimator_estimates` (
    workspace_id, environment, estimate_id, created_at, created_by, title,
    pattern, monthly_requests, corpus_gb, requirements_json,
    requirements_hash, engine_version, rate_card_version, snapshot_date,
    rigor_pct, results_json
  ) VALUES (
    p_workspace_id, p_environment, p_estimate_id, current_timestamp(),
    p_created_by, p_title, p_pattern, CAST(p_monthly_requests AS BIGINT),
    CAST(p_corpus_gb AS DOUBLE), p_requirements_json, p_requirements_hash,
    p_engine_version, p_rate_card_version, CAST(p_snapshot_date AS DATE),
    CAST(p_rigor_pct AS INT), p_results_json
  );
END
""".strip()

    return [
        (f"procedure {catalog}.{schema}.cp_record_estimate", record_estimate),
        (
            f"grant {app_service_principal} execute on cp_record_estimate",
            f"GRANT EXECUTE ON PROCEDURE {fq}.`cp_record_estimate` "
            f"TO `{app_service_principal}`",
        ),
    ]

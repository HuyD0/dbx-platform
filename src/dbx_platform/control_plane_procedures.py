"""Security-definer write broker for human Mission Control decisions.

The Databricks App service principal and human groups receive no ``MODIFY`` on
the action ledger. Verified App user tokens call these narrowly scoped Unity
Catalog procedures with ``EXECUTE`` only. The procedures re-check the connected
user's account-group membership and record ``session_user()`` as the actor.
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
    operator_group: str,
    approver_group: str,
) -> list[tuple[str, str]]:
    """Return idempotent SQL SECURITY DEFINER procedure migrations."""

    catalog = _identifier(catalog)
    schema = _identifier(schema)
    operator_group = _principal(operator_group)
    approver_group = _principal(approver_group)
    fq = f"`{catalog}`.`{schema}`"
    operator_check = (
        f"(is_account_group_member('{operator_group}') "
        f"OR is_account_group_member('{approver_group}'))"
    )
    approver_check = f"is_account_group_member('{approver_group}')"
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
  IF NOT {operator_check} THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'A Mission Control operator or approver is required';
  END IF;
  IF p_proposer_email IS NULL
     OR lower(session_user()) <> lower(p_proposer_email) THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The connected user does not match the proposer';
  END IF;
  IF p_action_type NOT IN ({allowed_actions}) THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The proposed action type is not allowlisted';
  END IF;
  IF (p_action_type = 'token-revoke' AND p_risk <> 'HIGH')
     OR (p_action_type <> 'token-revoke' AND p_risk <> 'MEDIUM') THEN
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
      SET MESSAGE_TEXT = 'The typed confirmation phrase is not canonical';
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
       <> lower(session_user())
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
    p_proposer_id, session_user(), CAST(p_created_at AS TIMESTAMP),
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
  IN p_details_json STRING,
  IN p_event_at STRING
)
LANGUAGE SQL
SQL SECURITY DEFINER
MODIFIES SQL DATA
AS BEGIN ATOMIC
  DECLARE v_from_status STRING;
  IF NOT {operator_check} THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'A Mission Control operator or approver is required';
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
    session_user(), p_details_json, CAST(p_event_at AS TIMESTAMP)
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
  DECLARE v_risk STRING;
  DECLARE v_confirm_phrase STRING;
  DECLARE v_expires_at TIMESTAMP;
  IF NOT {approver_check} THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'A current Mission Control approver is required';
  END IF;
  IF p_approver_email IS NULL
     OR lower(session_user()) <> lower(p_approver_email) THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The connected user does not match the approver';
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
  SET v_risk = (
    SELECT max(risk) FROM {fq}.`action_requests`
    WHERE workspace_id = p_workspace_id
      AND environment = p_environment
      AND action_id = p_action_id
      AND plan_hash = p_plan_hash
  );
  SET v_confirm_phrase = (
    SELECT max(confirm_phrase) FROM {fq}.`action_requests`
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
  IF p_target_status = 'APPROVED'
     AND v_risk IN ('MEDIUM', 'HIGH')
     AND p_confirmation <> v_confirm_phrase THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'The exact typed confirmation is required';
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
    p_decision, p_approver_id, session_user(), 'approver',
    nullif(p_confirmation, ''), CAST(p_decided_at AS TIMESTAMP)
  );
  INSERT INTO {fq}.`action_events` (
    workspace_id, environment, event_id, action_id, event_type, from_status,
    to_status, actor_id, details_json, event_ts
  ) VALUES (
    p_workspace_id, p_environment, p_event_id, p_action_id,
    concat('STATUS_', p_target_status), p_expected_status, p_target_status,
    session_user(), p_details_json, CAST(p_decided_at AS TIMESTAMP)
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
  IN p_details_json STRING,
  IN p_event_at STRING
)
LANGUAGE SQL
SQL SECURITY DEFINER
MODIFIES SQL DATA
AS BEGIN ATOMIC
  IF NOT {operator_check} THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'A Mission Control operator or approver is required';
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
    nullif(p_from_status, ''), nullif(p_to_status, ''), session_user(),
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
        if name != "cp_decide_action":
            statements.append(
                (
                    f"grant {operator_group} execute on {name}",
                    f"GRANT EXECUTE ON PROCEDURE {fq}.`{name}` "
                    f"TO `{operator_group}`",
                )
            )
        statements.append(
            (
                f"grant {approver_group} execute on {name}",
                f"GRANT EXECUTE ON PROCEDURE {fq}.`{name}` "
                f"TO `{approver_group}`",
            )
        )
    return statements

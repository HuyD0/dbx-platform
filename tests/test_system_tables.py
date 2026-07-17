"""Error-path tests for run_query. The Databricks SDK client is stubbed out —
no network, matching the rest of the suite."""

from types import SimpleNamespace

import pytest
from databricks.sdk.service.sql import StatementState

from dbx_platform.system_tables import SystemTablesUnavailableError, run_query


def _client_returning(state: StatementState, error_message: str | None = None):
    resp = SimpleNamespace(
        statement_id="stmt-1",
        status=SimpleNamespace(
            state=state,
            error=SimpleNamespace(message=error_message) if error_message else None,
        ),
        manifest=None,
        result=None,
    )
    return SimpleNamespace(
        statement_execution=SimpleNamespace(execute_statement=lambda **_: resp)
    )


def test_missing_warehouse_id_rejected():
    with pytest.raises(ValueError, match="warehouse ID is required"):
        run_query(_client_returning(StatementState.SUCCEEDED), "SELECT 1", warehouse_id="")


def test_missing_system_schema_grant_raises_actionable_error():
    w = _client_returning(
        StatementState.FAILED,
        "[INSUFFICIENT_PERMISSIONS] Insufficient privileges: User does not have "
        "USE SCHEMA on Schema 'system.lakeflow'. SQLSTATE: 42501",
    )
    with pytest.raises(SystemTablesUnavailableError) as exc:
        run_query(w, "SELECT 1 FROM system.lakeflow.jobs", warehouse_id="wh-1")
    # The hint must name both privileges — SELECT alone does not fix this error —
    # and point at the run-as principal, since scheduled jobs don't run as "you".
    message = str(exc.value)
    assert "GRANT USE SCHEMA, SELECT" in message
    assert "run-as" in message


def test_non_system_table_failure_is_plain_runtime_error():
    w = _client_returning(StatementState.FAILED, "DIVISION_BY_ZERO at line 3")
    with pytest.raises(RuntimeError) as exc:
        run_query(w, "SELECT 1 FROM system.billing.usage", warehouse_id="wh-1")
    assert not isinstance(exc.value, SystemTablesUnavailableError)

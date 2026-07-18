"""Exception -> JSON error taxonomy.

Every failure surfaces as {error, message, hint} with a machine-readable
error code the frontend keys its guided-setup states on, instead of leaking
raw tracebacks into the UI.
"""

from __future__ import annotations

import logging

from databricks.sdk.errors import NotFound, PermissionDenied
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from dbx_platform.system_tables import SystemTablesUnavailableError

log = logging.getLogger("platform_console")

_RUNBOOK_HINT = "See docs/runbook.md for the grants the app's service principal needs."
_SETUP_HINT = (
    "Run the '[dbx-platform] dashboards-setup' job (or `dbx-platform dashboards setup`) "
    "to create the findings tables, then run the digest job."
)
USER_AUTH_HINT = (
    "Confirm Databricks Apps user authorization is enabled, the app has been "
    "restarted after scope changes, and the current browser session has "
    "consented to the app's requested scopes."
)


def payload(error: str, message: str, hint: str | None = None) -> dict:
    body: dict = {"error": error, "message": message}
    if hint:
        body["hint"] = hint
    return body


def install(app: FastAPI) -> None:
    from backend.identity import UnauthenticatedError, UnauthorizedError

    @app.exception_handler(UnauthenticatedError)
    def _unauthenticated(
        request: Request, exc: UnauthenticatedError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content=payload("unauthenticated", str(exc), USER_AUTH_HINT),
        )

    @app.exception_handler(UnauthorizedError)
    def _unauthorized(request: Request, exc: UnauthorizedError) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content=payload("unauthorized", str(exc)),
        )

    @app.exception_handler(SystemTablesUnavailableError)
    def _system_tables(request: Request, exc: SystemTablesUnavailableError) -> JSONResponse:
        log.info("system tables unavailable", exc_info=exc)
        return JSONResponse(
            status_code=503,
            content=payload(
                "system_tables_unavailable",
                "Required Databricks system-table data is unavailable.",
                _RUNBOOK_HINT,
            ),
        )

    @app.exception_handler(PermissionDenied)
    def _permission(request: Request, exc: PermissionDenied) -> JSONResponse:
        log.info("workspace permission denied", exc_info=exc)
        return JSONResponse(
            status_code=403,
            content=payload(
                "permission_missing",
                "The app does not have permission to read this dependency.",
                _RUNBOOK_HINT,
            ),
        )

    @app.exception_handler(NotFound)
    def _not_found(request: Request, exc: NotFound) -> JSONResponse:
        log.info("workspace dependency not found", exc_info=exc)
        return JSONResponse(
            status_code=404,
            content=payload(
                "not_found",
                "The requested workspace dependency was not found.",
                _RUNBOOK_HINT,
            ),
        )

    @app.exception_handler(TimeoutError)
    def _timeout(request: Request, exc: TimeoutError) -> JSONResponse:
        log.warning("workspace dependency timed out", exc_info=exc)
        return JSONResponse(
            status_code=504,
            content=payload(
                "query_timeout",
                "The dependency did not respond before the timeout.",
                "Retry shortly or check source health in Settings.",
            ),
        )

    @app.exception_handler(ValueError)
    def _value_error(request: Request, exc: ValueError) -> JSONResponse:
        message = str(exc)
        if "warehouse" in message.lower():
            return JSONResponse(status_code=503, content=payload(
                "warehouse_not_configured", message,
                "Check the sql-warehouse resource binding in resources/app.yml."))
        return JSONResponse(status_code=400, content=payload("bad_request", message))

    @app.exception_handler(RuntimeError)
    def _runtime(request: Request, exc: RuntimeError) -> JSONResponse:
        message = str(exc)
        if "TABLE_OR_VIEW_NOT_FOUND" in message:
            log.info("platform table is missing", exc_info=exc)
            return JSONResponse(
                status_code=404,
                content=payload(
                    "findings_table_missing",
                    "A required Mission Control table has not been created.",
                    _SETUP_HINT,
                ),
            )
        log.exception("unhandled RuntimeError", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=payload(
                "internal",
                "Mission Control could not load this capability.",
                "Check dependency health in Settings and retry.",
            ),
        )

    @app.exception_handler(Exception)
    def _internal(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=payload(
                "internal",
                "Mission Control could not load this capability.",
                "Check dependency health in Settings and retry.",
            ),
        )

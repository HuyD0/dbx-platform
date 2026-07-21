"""Lazy singletons and small config helpers.

The workspace client and settings are created on first use, never at import
time — tests/test_app.py enforces this statically, and it keeps app startup
independent of workspace availability.
"""

from __future__ import annotations

import os
import time
from functools import lru_cache

from fastapi import Request

from dbx_platform.client import get_client
from dbx_platform.config import Settings


@lru_cache(maxsize=1)
def get_ws():
    return get_client(None)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


def now_ms() -> int:
    return int(time.time() * 1000)


def warehouse_id() -> str:
    return os.environ.get("DBX_PLATFORM_WAREHOUSE_ID", "") or get_settings().warehouse_id


def findings_table() -> str:
    s = get_settings()
    return f"{s.dashboard_catalog}.{s.dashboard_schema}.platform_findings"


def digest_table() -> str:
    s = get_settings()
    return f"{s.dashboard_catalog}.{s.dashboard_schema}.platform_digest"


def actions_enabled() -> bool:
    """Deployment-level opt-in for the guarded remediation actions, reviewed
    in git via app.yaml. Off by default: the console stays report-only."""
    return os.environ.get("DBX_PLATFORM_CONSOLE_ACTIONS", "").strip().lower() == "true"


def is_local_or_test() -> bool:
    """True only outside a Databricks App/runtime.

    Local repositories are proposal-only; this check must never be used to
    authorize a mutation.
    """
    return not (
        os.environ.get("DATABRICKS_APP_NAME")
        or os.environ.get("DATABRICKS_RUNTIME_VERSION")
    )


def control_plane_scope() -> tuple[str, str]:
    environment = os.environ.get("DBX_PLATFORM_ENVIRONMENT", "dev").strip() or "dev"
    explicit = os.environ.get("DATABRICKS_WORKSPACE_ID", "").strip()
    if explicit:
        return explicit, environment
    if is_local_or_test():
        return "local", environment
    return str(get_ws().get_workspace_id()), environment


@lru_cache(maxsize=1)
def get_control_plane_repository():
    """Return durable SQL in Databricks and proposal-only memory when local.

    ``memory`` is rejected inside a Databricks App/runtime so a configuration
    error can never silently downgrade approval durability.
    """
    from backend.control_plane_repository import (
        InMemoryControlPlaneRepository,
        SQLControlPlaneRepository,
    )

    mode = os.environ.get("DBX_PLATFORM_CONTROL_PLANE_REPOSITORY", "auto").lower()
    if mode not in {"auto", "memory", "sql"}:
        raise ValueError(
            "DBX_PLATFORM_CONTROL_PLANE_REPOSITORY must be auto, memory, or sql."
        )
    local = is_local_or_test()
    if mode == "memory" and not local:
        raise RuntimeError(
            "The in-memory control-plane repository is forbidden in Databricks."
        )
    if mode == "memory" or (mode == "auto" and local):
        workspace_id, environment = control_plane_scope()
        return InMemoryControlPlaneRepository(workspace_id, environment)
    auto_migrate = os.environ.get(
        "DBX_PLATFORM_CONTROL_PLANE_AUTO_MIGRATE", ""
    ).lower() == "true"
    if auto_migrate and not local:
        raise RuntimeError(
            "Runtime control-plane DDL is forbidden. Run the deployment-only "
            "schema_migrations Job before starting the App."
        )
    settings = get_settings()
    workspace_id, environment = control_plane_scope()
    return SQLControlPlaneRepository(
        get_ws(),
        warehouse_id(),
        settings.dashboard_catalog,
        settings.dashboard_schema,
        auto_migrate=auto_migrate,
        workspace_id=workspace_id,
        environment=environment,
    )


def get_user_control_plane_repository(request: Request):
    """Return the procedure-only repository after request identity verification.

    The route must first verify the forwarded user and live group membership,
    storing the resulting actor on the request. Writes then use the App service
    principal to call the security-definer procedures; human SQL sessions have
    no procedure ``EXECUTE`` and the App identity has no table ``MODIFY``.
    Actor fields always come from the verified request identity.
    """
    if is_local_or_test():
        return get_control_plane_repository()
    actor = getattr(request.state, "actor", None)
    if actor is None or not actor.actor_id:
        raise RuntimeError(
            "A verified Databricks App user is required for control-plane writes."
        )
    settings = get_settings()
    workspace_id, environment = control_plane_scope()
    from backend.control_plane_repository import SQLControlPlaneRepository

    return SQLControlPlaneRepository(
        get_ws(),
        warehouse_id(),
        settings.dashboard_catalog,
        settings.dashboard_schema,
        auto_migrate=False,
        workspace_id=workspace_id,
        environment=environment,
    )


@lru_cache(maxsize=1)
def get_identity_verifier():
    from backend.identity import IdentityVerifier

    return IdentityVerifier(
        get_ws,
        approver_group=os.environ.get(
            "DBX_PLATFORM_APPROVER_GROUP", "dbx-platform-approvers"
        ),
        operator_group=os.environ.get(
            "DBX_PLATFORM_OPERATOR_GROUP", "dbx-platform-operators"
        ),
        allow_local_identity=is_local_or_test(),
    )


def require_verified_user(request: Request):
    """FastAPI dependency for APIs that expose workspace operational data."""
    actor = getattr(request.state, "actor", None)
    if actor is not None:
        return actor
    actor = get_identity_verifier().verify(request)
    request.state.actor = actor
    return actor


def require_operator(request: Request):
    """Require a verified operator/proposer for AI-derived sensitive text."""
    actor = require_verified_user(request)
    if not actor.has_role("proposer"):
        from backend.identity import UnauthorizedError

        raise UnauthorizedError(
            "Membership in dbx-platform-operators or "
            "dbx-platform-approvers is required."
        )
    return actor


def power_controller_job_id() -> int:
    raw = os.environ.get("DBX_PLATFORM_POWER_CONTROLLER_JOB_ID", "").strip()
    if not raw:
        raise ValueError(
            "DBX_PLATFORM_POWER_CONTROLLER_JOB_ID is not configured from the "
            "power-controller app resource binding."
        )
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(
            "DBX_PLATFORM_POWER_CONTROLLER_JOB_ID must be an integer job ID."
        ) from exc


@lru_cache(maxsize=1)
def get_runtime_controller_client():
    from backend.runtime_controller_client import RuntimeControllerClient

    return RuntimeControllerClient(
        get_ws(),
        power_controller_job_id(),
        get_control_plane_repository(),
    )


def action_executor_job_id() -> int:
    raw = os.environ.get("DBX_PLATFORM_ACTION_EXECUTOR_JOB_ID", "").strip()
    if not raw:
        raise ValueError(
            "DBX_PLATFORM_ACTION_EXECUTOR_JOB_ID is not configured from the "
            "action-executor app resource binding."
        )
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(
            "DBX_PLATFORM_ACTION_EXECUTOR_JOB_ID must be an integer job ID."
        ) from exc


@lru_cache(maxsize=1)
def get_action_executor_client():
    from backend.action_executor_client import ActionExecutorClient

    return ActionExecutorClient(get_ws(), action_executor_job_id())


def chat_model_endpoint() -> str:
    """Foundation-model endpoint the in-process chat agent reasons with.

    Bound as the app's ``chat-model`` resource, whose name is injected as
    DBX_PLATFORM_CHAT_MODEL_ENDPOINT; falls back to the digest model."""
    explicit = os.environ.get("DBX_PLATFORM_CHAT_MODEL_ENDPOINT", "").strip()
    if explicit:
        return explicit
    return get_settings().digest_model


def clamp_days(days: int, lo: int = 7, hi: int = 90) -> int:
    return max(lo, min(hi, days))

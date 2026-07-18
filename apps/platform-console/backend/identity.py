"""Verified Databricks App user identity and approver authorization."""

from __future__ import annotations

import json
import os
from collections.abc import Callable

from databricks.sdk import WorkspaceClient
from fastapi import Request

from backend.control_plane import Actor, ControlPlaneError


class UnauthenticatedError(ControlPlaneError):
    code = "unauthenticated"


class UnauthorizedError(ControlPlaneError):
    code = "unauthorized"


class IdentityVerifier:
    """Verify the forwarded user token against the workspace SCIM ``Me`` API.

    Forwarded identity headers are useful claims but are never sufficient for
    authorization. The access token is re-resolved server-side and approver
    membership comes from that response. ``X-Forwarded-User`` is intentionally
    not compared with the SCIM ID: Databricks documents it as the IdP-supplied
    user identifier, not as the workspace SCIM numeric ID.
    """

    def __init__(
        self,
        workspace_client_factory: Callable,
        *,
        approver_group: str = "dbx-platform-approvers",
        operator_group: str = "dbx-platform-operators",
        allow_local_identity: bool = False,
        user_workspace_client_factory: Callable[[str, str], object] | None = None,
    ) -> None:
        self.workspace_client_factory = workspace_client_factory
        self.approver_group = approver_group
        self.operator_group = operator_group
        self.allow_local_identity = allow_local_identity
        self.user_workspace_client_factory = (
            user_workspace_client_factory
            or (lambda host, token: WorkspaceClient(host=host, token=token))
        )

    def verify(
        self,
        request: Request,
        *,
        require_approver: bool = False,
        require_proposer: bool = False,
    ) -> Actor:
        if self.allow_local_identity and os.environ.get(
            "DBX_PLATFORM_LOCAL_IDENTITY", ""
        ).lower() == "true":
            actor_id = os.environ.get("DBX_PLATFORM_LOCAL_ACTOR_ID", "").strip()
            if not actor_id:
                raise UnauthenticatedError(
                    "DBX_PLATFORM_LOCAL_ACTOR_ID is required for local identity mode."
                )
            roles = {"authenticated", "viewer"}
            configured_roles = {
                value.strip().lower()
                for value in os.environ.get(
                    "DBX_PLATFORM_LOCAL_ROLES", "approver"
                ).split(",")
                if value.strip()
            }
            roles.update(configured_roles)
            if "approver" in roles or "operator" in roles:
                roles.add("proposer")
            actor = Actor(
                actor_id=actor_id,
                email=os.environ.get("DBX_PLATFORM_LOCAL_ACTOR_EMAIL") or None,
                roles=frozenset(roles),
            )
            if require_approver and not actor.has_role("approver"):
                raise UnauthorizedError(
                    f"Membership in {self.approver_group} is required."
                )
            if require_proposer and not actor.has_role("proposer"):
                raise UnauthorizedError(
                    f"Membership in {self.operator_group} or "
                    f"{self.approver_group} is required to propose actions."
                )
            return actor

        token = (
            request.headers.get("X-Forwarded-Access-Token")
            or request.headers.get("X-Databricks-Access-Token")
            or ""
        ).strip()
        if not token:
            raise UnauthenticatedError(
                "A Databricks App forwarded user access token is required."
            )

        try:
            app_client = self.workspace_client_factory()
            host = str(app_client.config.host or "").strip()
            if not host:
                raise RuntimeError("Workspace host is unavailable.")
            user_client = self.user_workspace_client_factory(host, token)
            me = user_client.api_client.do(
                "GET",
                "/api/2.0/preview/scim/v2/Me",
            )
        except Exception as exc:  # noqa: BLE001 - auth failures are intentionally uniform
            raise UnauthenticatedError(
                "The forwarded user identity could not be verified."
            ) from exc
        if not isinstance(me, dict) or not me.get("id"):
            raise UnauthenticatedError("The workspace returned no stable user identity.")

        actor_id = str(me["id"])
        groups = {
            str(group.get("display") or group.get("value") or "")
            for group in (me.get("groups") or [])
            if isinstance(group, dict)
        }
        emails = [
            str(item.get("value"))
            for item in (me.get("emails") or [])
            if isinstance(item, dict) and item.get("value")
        ]
        email = str(me.get("userName") or (emails[0] if emails else "")) or None
        roles = {"authenticated", "viewer"}
        if self.operator_group in groups:
            roles.update({"operator", "proposer"})
        if self.approver_group in groups:
            roles.update({"approver", "operator", "proposer"})
        actor = Actor(
            actor_id=actor_id,
            email=email,
            roles=frozenset(roles),
        )
        if require_approver and not actor.has_role("approver"):
            raise UnauthorizedError(f"Membership in {self.approver_group} is required.")
        if require_proposer and not actor.has_role("proposer"):
            raise UnauthorizedError(
                f"Membership in {self.operator_group} or "
                f"{self.approver_group} is required to propose actions."
            )
        return actor


_SENSITIVE_KEY_PARTS = (
    "email",
    "user",
    "principal",
    "owner",
    "creator",
    "created_by",
    "display_name",
    "token_id",
    "proposer",
    "approver",
    "actor_id",
    "requester",
)
_JSON_STRING_FIELDS = frozenset(
    {
        "details",
        "evidence_json",
        "affected_resources_json",
        "metadata_json",
        "prior_state_json",
    }
)


def _is_token_finding(value: dict) -> bool:
    action = str(
        value.get("proposed_action_type") or value.get("action") or ""
    ).lower()
    check = str(value.get("check_name") or "").lower()
    return "token" in check or action == "token-revoke"


def mask_for_viewer(value, actor: Actor):
    """Recursively redact identity-bearing fields for viewer-only users."""
    if actor.has_role("operator") or actor.has_role("approver"):
        return value
    if isinstance(value, list):
        return [mask_for_viewer(item, actor) for item in value]
    if isinstance(value, dict):
        masked = {}
        token_finding = _is_token_finding(value)
        for key, item in value.items():
            normalized = str(key).lower()
            if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                masked[key] = "[redacted]"
            elif token_finding and normalized == "resource":
                masked[key] = "[redacted]"
            elif token_finding and normalized == "affected_resources":
                masked[key] = [
                    {"resource_id": "[redacted]"}
                    for _resource in (item if isinstance(item, list) else [])
                ]
            elif normalized in _JSON_STRING_FIELDS and isinstance(item, str):
                try:
                    decoded = json.loads(item)
                except json.JSONDecodeError:
                    masked[key] = item
                else:
                    masked[key] = json.dumps(
                        mask_for_viewer(decoded, actor),
                        sort_keys=True,
                        separators=(",", ":"),
                    )
            else:
                masked[key] = mask_for_viewer(item, actor)
        return masked
    return value

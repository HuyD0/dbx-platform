"""Verified Platform Console identity adapters for upstream LakeMeter."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend import deps
from backend.lakemeter_database import get_db

FORWARDED_EMAIL_HEADER = "X-Forwarded-Email"
FORWARDED_USER_HEADER = "X-Forwarded-User"
FORWARDED_ACCESS_TOKEN_HEADER = "X-Forwarded-Access-Token"
EMAIL_HEADERS = [FORWARDED_EMAIL_HEADER]
USER_HEADERS = [FORWARDED_USER_HEADER]


def get_user_from_headers(request: Request) -> tuple[str | None, str | None]:
    """Compatibility helper backed only by the already verified actor."""
    actor = getattr(request.state, "actor", None)
    if actor is None:
        return None, None
    return actor.email, actor.actor_id


def get_or_create_user(db: Session, email: str, full_name: str | None = None):
    from app.models.user import User

    user = db.query(User).filter(User.email == email).first()
    now = datetime.now(UTC).replace(tzinfo=None)
    if user is not None:
        if user.last_login_at is None or (now - user.last_login_at).total_seconds() > 300:
            user.last_login_at = now
            db.commit()
        return user
    user = User(
        user_id=uuid4(),
        email=email,
        full_name=full_name,
        role="user",
        is_active=True,
        last_login_at=now,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_current_user(request: Request, db: Session = Depends(get_db)):  # noqa: B008
    """Resolve LakeMeter ownership from the SCIM-verified request actor."""
    actor = getattr(request.state, "actor", None)
    if actor is None or not actor.actor_id or not actor.email:
        raise HTTPException(
            status_code=401,
            detail="A verified Databricks user email is required.",
        )
    user = get_or_create_user(db, actor.email, actor.email.split("@", 1)[0])
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is disabled.")
    return user


def get_optional_user(request: Request, db: Session = Depends(get_db)):  # noqa: B008
    try:
        return get_current_user(request, db)
    except HTTPException:
        return None


def debug_headers(request: Request) -> dict:
    actor = getattr(request.state, "actor", None)
    return {
        "verified": actor is not None,
        "actor_id": actor.actor_id if actor else None,
    }


class _TokenManagerBridge:
    """Supply the app SP client only when the estimate assistant is invoked."""

    @property
    def _workspace_client(self):
        return deps.get_ws()


token_manager = _TokenManagerBridge()


def init_token_manager() -> _TokenManagerBridge:
    return token_manager

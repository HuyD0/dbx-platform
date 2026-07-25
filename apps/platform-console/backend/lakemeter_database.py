"""LakeMeter SQLAlchemy bridge for OAuth-only Lakebase access.

This module intentionally does no network or database work at import time.
The upstream ``app.database`` module is replaced with this module before any
LakeMeter models or routers are imported.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator

from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

Base = declarative_base()

_engine: Engine | None = None
_session_factory: sessionmaker | None = None
_engine_lock = threading.Lock()


def _configured() -> bool:
    if os.environ.get("DATABASE_URL", "").strip():
        return True
    return all(
        os.environ.get(name, "").strip()
        for name in ("PGHOST", "PGDATABASE", "PGUSER", "DBX_LAKEMETER_ENDPOINT")
    )


def _oauth_connection():
    """Open one Postgres connection with a fresh, one-hour OAuth credential."""
    import psycopg2

    from backend import deps

    endpoint = os.environ["DBX_LAKEMETER_ENDPOINT"].strip()
    credential = deps.get_ws().postgres.generate_database_credential(endpoint)
    token = str(credential.token or "")
    if not token:
        raise RuntimeError("Lakebase returned no OAuth database credential.")
    return psycopg2.connect(
        host=os.environ["PGHOST"],
        port=int(os.environ.get("PGPORT", "5432")),
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=token,
        sslmode=os.environ.get("PGSSLMODE", "require"),
        connect_timeout=10,
        application_name=os.environ.get("PGAPPNAME", "dbx-platform-lakemeter"),
    )


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is not None:
        return _engine
    if not _configured():
        raise RuntimeError(
            "LakeMeter Lakebase is not configured. Attach the bundle-owned "
            "Postgres app resource or set DATABASE_URL for local development."
        )
    with _engine_lock:
        if _engine is not None:
            return _engine
        direct_url = os.environ.get("DATABASE_URL", "").strip()
        if direct_url:
            engine = create_engine(direct_url, pool_pre_ping=True)
        else:
            engine = create_engine(
                "postgresql+psycopg2://",
                creator=_oauth_connection,
                pool_pre_ping=True,
                pool_recycle=2700,
                pool_size=10,
                max_overflow=10,
                pool_timeout=15,
            )
        _engine = engine
        _session_factory = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=engine,
        )
        return engine


def refresh_engine() -> bool:
    global _engine, _session_factory
    with _engine_lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _session_factory = None
    try:
        get_engine()
    except Exception:
        return False
    return True


def get_db() -> Iterator[Session]:
    try:
        get_engine()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"LakeMeter database unavailable: {type(exc).__name__}",
        ) from exc
    if _session_factory is None:
        raise HTTPException(status_code=503, detail="LakeMeter database unavailable.")
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()


def schema_status(expected_version: int) -> tuple[bool, int | None, str | None]:
    """Return readiness without ever applying runtime DDL."""
    if not _configured():
        return False, None, "database_not_configured"
    try:
        with get_engine().connect() as connection:
            version = connection.execute(
                text(
                    "SELECT value FROM lakemeter.integration_metadata "
                    "WHERE key = 'schema_version'"
                )
            ).scalar_one_or_none()
    except Exception as exc:
        return False, None, f"database_unavailable:{type(exc).__name__}"
    try:
        actual = int(version) if version is not None else None
    except (TypeError, ValueError):
        return False, None, "invalid_schema_version"
    if actual != expected_version:
        return False, actual, "schema_migration_required"
    return True, actual, None


# Compatibility attributes imported by the upstream debug app. The standalone
# app and debug routes are not mounted, but retaining these names makes the
# adapter resilient to harmless upstream imports.
engine = None
SessionLocal = None

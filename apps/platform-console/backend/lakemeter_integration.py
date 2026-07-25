"""Lazy, same-process LakeMeter integration boundary."""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse

from backend import lakemeter_database, lakemeter_identity

APP_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = APP_DIR.parents[1]
STAGED = APP_DIR / "lakemeter_vendor"
SOURCE = REPO_ROOT / "vendor" / "lakemeter"
router = APIRouter(prefix="/api/lakemeter", tags=["lakemeter"])


def vendor_root() -> Path:
    if (STAGED / "backend" / "app").is_dir():
        return STAGED
    return SOURCE


def lock_path() -> Path:
    staged = STAGED / "upstream.lock.json"
    if staged.is_file():
        return staged
    return REPO_ROOT / "integrations" / "lakemeter" / "upstream.lock.json"


def read_lock() -> dict:
    try:
        return json.loads(lock_path().read_text())
    except (OSError, ValueError):
        return {}


def pricing_directory() -> Path:
    built = APP_DIR / "static" / "pricing"
    if built.is_dir():
        return built
    return SOURCE / "backend" / "static" / "pricing"


@router.get("/status")
def status() -> dict:
    lock = read_lock()
    expected = int(lock.get("schema_version") or 0)
    ready, actual, reason = lakemeter_database.schema_status(expected)
    frontend_ready = (APP_DIR / "static" / "lakemeter" / "entry.js").is_file()
    return {
        "status": "ready" if ready and frontend_ready else "unavailable",
        "ready": ready and frontend_ready,
        "database_ready": ready,
        "frontend_ready": frontend_ready,
        "reason": reason if not ready else (None if frontend_ready else "frontend_not_built"),
        "schema_version": actual,
        "required_schema_version": expected,
        "upstream_version": lock.get("tag"),
        "upstream_commit": lock.get("commit"),
        "pricing_version": lock.get("pricing_version"),
    }


def _install_module_bridges() -> Path:
    backend_root = vendor_root() / "backend"
    if not (backend_root / "app").is_dir():
        raise RuntimeError("Pinned LakeMeter backend is missing from the app artifact.")
    value = str(backend_root)
    if value not in sys.path:
        sys.path.insert(0, value)
    importlib.import_module("app")
    sys.modules["app.database"] = lakemeter_database
    sys.modules["app.auth.databricks_auth"] = lakemeter_identity
    sys.modules["app.auth.token_manager"] = lakemeter_identity
    return backend_root


def build_upstream_app() -> FastAPI:
    _install_module_bridges()
    routes = importlib.import_module("app.routes")
    chat = importlib.import_module("app.routes.chat")
    app = FastAPI(
        title="LakeMeter API (embedded)",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    for upstream_router in (
        routes.estimates_router,
        routes.line_items_router,
        routes.workload_types_router,
        routes.export_router,
        routes.vm_pricing_router,
        routes.calculate_router,
        routes.reference_router,
        chat.router,
    ):
        app.include_router(upstream_router)
    return app


class LazyLakeMeterApp:
    """Initialize upstream imports only after a verified request reaches them."""

    def __init__(self) -> None:
        self._app = None
        self._error: Exception | None = None
        self._lock = asyncio.Lock()

    async def _load(self):
        if self._app is not None or self._error is not None:
            return
        async with self._lock:
            if self._app is not None or self._error is not None:
                return
            try:
                self._app = build_upstream_app()
            except Exception as exc:  # noqa: BLE001 - surfaced as stable 503
                self._error = exc

    async def __call__(self, scope, receive, send):
        await self._load()
        if self._app is None:
            response = JSONResponse(
                status_code=503,
                content={
                    "error": "lakemeter_unavailable",
                    "message": "The embedded estimator backend could not initialize.",
                    "reason": type(self._error).__name__ if self._error else "unknown",
                },
            )
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)


IDENTITY_HEADERS = frozenset(
    {
        b"x-forwarded-email",
        b"x-forwarded-preferred-username",
        b"x-forwarded-user",
        b"x-forwarded-user-id",
        b"x-forwarded-name",
        b"x-forwarded-access-token",
        b"x-databricks-access-token",
        b"x-databricks-user-id",
        b"x-databricks-user-email",
    }
)


def canonicalize_identity_headers(request) -> None:
    """Remove spoofable identity claims and inject the verified SCIM actor."""
    actor = getattr(request.state, "actor", None)
    if actor is None:
        return
    headers = [
        (name, value)
        for name, value in request.scope.get("headers", [])
        if name.lower() not in IDENTITY_HEADERS
    ]
    if actor.email:
        headers.append((b"x-forwarded-email", actor.email.encode()))
    headers.append((b"x-forwarded-user", actor.actor_id.encode()))
    request.scope["headers"] = headers

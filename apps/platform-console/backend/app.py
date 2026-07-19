"""FastAPI application factory + SPA mount."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from backend import deps, errors
from backend.errors import USER_AUTH_HINT, payload
from backend.identity import (
    UnauthenticatedError,
    UnauthorizedError,
    mask_for_viewer,
)
from backend.routers import (
    ai_governance,
    chat,
    control_plane,
    cost,
    digest,
    governance,
    housekeeping,
    jobs,
    llm_cost,
    meta,
    ml,
    overview,
    performance,
    security,
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="Platform Console", docs_url=None, redoc_url=None, openapi_url=None)
    errors.install(app)

    @app.middleware("http")
    async def verified_api_boundary(request: Request, call_next):
        """Authenticate operational APIs and redact structured viewer output."""
        if not request.url.path.startswith("/api/") or request.url.path == "/api/health":
            return await call_next(request)
        try:
            actor = await run_in_threadpool(
                deps.get_identity_verifier().verify,
                request,
            )
        except UnauthenticatedError as exc:
            return JSONResponse(
                status_code=401,
                content=payload("unauthenticated", str(exc), USER_AUTH_HINT),
            )
        except UnauthorizedError as exc:
            return JSONResponse(
                status_code=403,
                content=payload("unauthorized", str(exc)),
            )
        request.state.actor = actor
        response = await call_next(request)
        if actor.has_role("operator") or actor.has_role("approver"):
            return response
        if not response.headers.get("content-type", "").startswith(
            "application/json"
        ):
            return response
        chunks = [chunk async for chunk in response.body_iterator]
        body = b"".join(
            chunk.encode() if isinstance(chunk, str) else chunk for chunk in chunks
        )
        headers = dict(response.headers)
        headers.pop("content-length", None)
        try:
            document = json.loads(body)
        except (TypeError, ValueError):
            return Response(
                content=body,
                status_code=response.status_code,
                headers=headers,
                background=response.background,
            )
        return JSONResponse(
            content=mask_for_viewer(document, actor),
            status_code=response.status_code,
            headers=headers,
            background=response.background,
        )

    for module in (meta, overview, control_plane, cost, llm_cost, housekeeping, security,
                   governance, ai_governance, ml, performance, digest, jobs, chat):
        app.include_router(module.router)

    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    def unknown_api(path: str) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=payload(
                "capability_not_available",
                f"The API capability '/api/{path}' is not implemented.",
                "Check source health and deployment version in Settings.",
            ),
        )

    index = STATIC_DIR / "index.html"
    if index.exists():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            # Client-side routing fallback: real files (favicon etc.) win,
            # everything else gets index.html.
            candidate = (STATIC_DIR / path).resolve()
            if path and candidate.is_file() and candidate.is_relative_to(STATIC_DIR):
                return FileResponse(candidate)
            return FileResponse(index)
    else:
        @app.get("/", include_in_schema=False)
        def no_frontend() -> JSONResponse:
            return JSONResponse({
                "message": "Platform Console API is running, but the frontend build "
                           "is missing. Build it with: cd frontend && npm ci && "
                           "npm run build",
            })

    return app

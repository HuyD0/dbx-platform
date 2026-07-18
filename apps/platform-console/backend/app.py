"""FastAPI application factory + SPA mount."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import errors
from backend.routers import (
    actions,
    chat,
    cost,
    digest,
    governance,
    housekeeping,
    jobs,
    meta,
    ml,
    overview,
    security,
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="Platform Console", docs_url=None, redoc_url=None, openapi_url=None)
    errors.install(app)

    for module in (meta, overview, cost, housekeeping, security, governance,
                   ml, digest, jobs, actions, chat):
        app.include_router(module.router)

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

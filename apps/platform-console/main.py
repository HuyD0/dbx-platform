"""Launcher for the Platform Console Databricks App.

Databricks Apps inject the port via DATABRICKS_APP_PORT and require the
server to bind 0.0.0.0. app.yaml's command is an argv array with no shell,
so the env var is read here rather than expanded on the command line.
"""

from __future__ import annotations

import os

import uvicorn
from backend.app import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("DATABRICKS_APP_PORT", "8000")))

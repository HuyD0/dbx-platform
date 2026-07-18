"""Local artifact discovery.

Direct Unity Catalog Volume publication is disabled. Bundle deployment uploads
its own wheel; any separate Volume release needs a future allowlisted action.
"""

from __future__ import annotations

from pathlib import Path

from databricks.sdk import WorkspaceClient


def find_wheel(dist_dir: str | Path = "dist") -> Path:
    wheels = sorted(Path(dist_dir).glob("dbx_platform-*.whl"))
    if not wheels:
        raise FileNotFoundError(
            f"No wheel found in {dist_dir}/. Build one first: python -m build --wheel"
        )
    return wheels[-1]


def publish_wheel(w: WorkspaceClient, volume_dir: str, wheel_path: str | None = None) -> str:
    """Disabled compatibility entrypoint; never writes to the workspace."""

    del w, volume_dir, wheel_path
    raise RuntimeError(
        "Direct wheel publication is disabled; use an approved release workflow."
    )

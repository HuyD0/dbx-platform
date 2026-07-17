"""Publish the built wheel to a Unity Catalog Volume.

Bundle-deployed jobs don't need this — the bundle uploads the wheel itself.
The Volume copy is for reuse from interactive notebooks and other clusters:

    %pip install /Volumes/<catalog>/<schema>/<volume>/wheels/dbx_platform-<ver>-py3-none-any.whl
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
    """Upload the wheel to a UC Volume directory and return the Volume path."""
    if not volume_dir.startswith("/Volumes/"):
        raise ValueError(f"Volume path must start with /Volumes/, got: {volume_dir}")
    wheel = Path(wheel_path) if wheel_path else find_wheel()
    dest = f"{volume_dir.rstrip('/')}/{wheel.name}"
    with open(wheel, "rb") as fh:
        w.files.upload(dest, fh, overwrite=True)
    return dest

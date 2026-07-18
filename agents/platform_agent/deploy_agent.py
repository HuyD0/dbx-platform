"""Build helper for the platform agent.

Direct MLflow registration and endpoint deployment are disabled in v1. Model
registration, promotion, and serving changes require a dedicated allowlisted
Mission Control executor action; that action is not enabled yet.
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent


def model_code_paths() -> list[str]:
    """Files a future approved model-deploy action must package."""

    return [str(HERE), str(REPO_ROOT / "src")]


def main() -> None:
    raise SystemExit(
        "Direct agent registration/deployment is disabled. Add and approve an "
        "allowlisted Mission Control model-deploy action before enabling this helper."
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Stage pinned LakeMeter backend inputs inside the Databricks App source."""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "lakemeter"
DESTINATION = ROOT / "apps" / "platform-console" / "lakemeter_vendor"
STATIC_PRICING = ROOT / "apps" / "platform-console" / "static" / "pricing"


def main() -> int:
    required = (
        VENDOR / "backend" / "app",
        VENDOR / "backend" / "static" / "pricing",
        ROOT / "integrations" / "lakemeter" / "upstream.lock.json",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit(f"LakeMeter is not vendored; missing: {missing}")
    if DESTINATION.exists():
        shutil.rmtree(DESTINATION)
    (DESTINATION / "backend").mkdir(parents=True)
    shutil.copytree(VENDOR / "backend" / "app", DESTINATION / "backend" / "app")
    shutil.copy2(
        ROOT / "integrations" / "lakemeter" / "upstream.lock.json",
        DESTINATION / "upstream.lock.json",
    )
    shutil.copy2(VENDOR / "LICENSE.md", DESTINATION / "LICENSE.md")
    shutil.copy2(VENDOR / "NOTICE.md", DESTINATION / "NOTICE.md")
    if STATIC_PRICING.exists():
        shutil.rmtree(STATIC_PRICING)
    STATIC_PRICING.mkdir(parents=True)
    for source in (VENDOR / "backend" / "static" / "pricing").iterdir():
        # Databricks Apps reject individual files over 10 MB. The frontend
        # intentionally retrieves VM prices through /api/v1/vm-pricing and
        # never fetches the large raw VM CSV.
        if source.is_file() and source.stat().st_size < 9_500_000:
            shutil.copy2(source, STATIC_PRICING / source.name)
    print(f"Staged LakeMeter backend at {DESTINATION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

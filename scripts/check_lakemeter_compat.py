#!/usr/bin/env python3
"""Fail closed when the pinned LakeMeter snapshot no longer fits its adapters."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "lakemeter"
LOCK_PATH = ROOT / "integrations" / "lakemeter" / "upstream.lock.json"

REQUIRED_API_FILES = (
    "estimates.py",
    "line_items.py",
    "workload_types.py",
    "vm_pricing.py",
    "chat.py",
)
REQUIRED_FRONTEND_ROUTES = (
    "/",
    "calculator",
    "calculator/:id",
    "estimate/:id",
    "pricing",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(
        item
        for item in root.rglob("*")
        if item.is_file()
        and "__pycache__" not in item.parts
        and item.suffix not in {".pyc", ".pyo"}
    ):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest()


def _function_sql_count() -> int:
    count = 0
    for source_file in sorted((VENDOR / "scripts" / "functions").glob("*.py")):
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if any(
                isinstance(target, ast.Name) and target.id == "create_function_sql"
                for target in node.targets
            ):
                value = ast.literal_eval(node.value)
                if "CREATE OR REPLACE FUNCTION" not in value:
                    raise RuntimeError(f"Invalid function SQL in {source_file.name}")
                count += 1
    return count


def check() -> dict[str, object]:
    failures: list[str] = []
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if not re.fullmatch(r"v\d+\.\d+\.\d+", str(lock.get("tag", ""))):
        failures.append("upgrade source is not a stable SemVer release tag")
    if _tree_sha256(VENDOR) != lock.get("vendored_tree_sha256"):
        failures.append("vendored snapshot differs from the lock manifest")
    if _sha256(VENDOR / "LICENSE.md") != lock.get("license_sha256"):
        failures.append("upstream license checksum differs from the lock manifest")

    routes = VENDOR / "backend" / "app" / "routes"
    for filename in REQUIRED_API_FILES:
        if not (routes / filename).is_file():
            failures.append(f"required upstream API module is missing: {filename}")

    embedded = (
        ROOT / "integrations" / "lakemeter" / "frontend" / "src" / "entry.tsx"
    ).read_text(encoding="utf-8")
    for route in REQUIRED_FRONTEND_ROUTES:
        if f'path="{route}"' not in embedded:
            failures.append(f"embedded frontend route adapter is missing: {route}")
    if 'basename="/cost/estimator"' not in embedded:
        failures.append("embedded router basename changed")
    if "attachShadow" not in (
        ROOT
        / "apps"
        / "platform-console"
        / "frontend"
        / "src"
        / "pages"
        / "LakeMeter.tsx"
    ).read_text(encoding="utf-8"):
        failures.append("LakeMeter is no longer isolated by Shadow DOM")

    integration = (
        ROOT / "apps" / "platform-console" / "backend" / "lakemeter_integration.py"
    ).read_text(encoding="utf-8")
    for excluded in ("users.router", "CORSMiddleware", "StaticFiles"):
        if excluded in integration:
            failures.append(f"excluded upstream surface is mounted: {excluded}")

    function_count = _function_sql_count()
    if function_count < 10:
        failures.append("upstream calculation function set is incomplete")
    oversized = [
        str(path.relative_to(ROOT))
        for path in (ROOT / "apps" / "platform-console").rglob("*")
        if path.is_file()
        and not {".venv", "node_modules", "wheels"}.intersection(path.parts)
        and path.stat().st_size >= 9_500_000
    ]
    if oversized:
        failures.append(f"Databricks App files exceed the safe size limit: {oversized}")

    report = {
        "status": "compatible" if not failures else "blocked",
        "tag": lock.get("tag"),
        "commit": lock.get("commit"),
        "schema_version": lock.get("schema_version"),
        "pricing_version": lock.get("pricing_version"),
        "function_count": function_count,
        "failures": failures,
    }
    if failures:
        raise RuntimeError(json.dumps(report, sort_keys=True))
    return report


def main() -> int:
    try:
        print(json.dumps(check(), indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

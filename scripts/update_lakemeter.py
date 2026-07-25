#!/usr/bin/env python3
"""Import an immutable, minimal LakeMeter release snapshot.

The vendored tree is deliberately never patched. Mission Control-specific
frontend, authentication, database, and deployment behavior lives under
``integrations/lakemeter`` and ``apps/platform-console/backend``.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "lakemeter"
LOCK = ROOT / "integrations" / "lakemeter" / "upstream.lock.json"
PRICING_ASSETS = ROOT / "src" / "dbx_platform" / "lakemeter_assets" / "pricing"
UPSTREAM = "https://github.com/databrickslabs/lakemeter-oss.git"
TAG_PATTERN = re.compile(r"^v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")

# Only runtime, build, and migration inputs are vendored. Documentation media,
# historical built JS bundles, tests, and ETL workbooks are intentionally
# omitted. The installer source is parsed by the governed migration adapter;
# it is never executed or staged into the App.
INCLUDE = (
    "LICENSE.md",
    "NOTICE.md",
    "VERSION",
    "backend/app",
    "backend/static/pricing",
    "frontend/index.html",
    "frontend/package-lock.json",
    "frontend/package.json",
    "frontend/postcss.config.js",
    "frontend/src",
    "frontend/tailwind.config.js",
    "frontend/tsconfig.json",
    "frontend/tsconfig.node.json",
    "scripts/functions",
    "scripts/install_lakemeter.py",
)


def run(*args: str, cwd: Path | None = None, capture: bool = False) -> str:
    completed = subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=capture,
        stdout=subprocess.PIPE if capture else None,
    )
    return completed.stdout.strip() if capture else ""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(root: Path) -> str:
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
        digest.update(bytes.fromhex(sha256(path)))
    return digest.hexdigest()


def safe_extract(archive: Path, destination: Path) -> None:
    destination_resolved = destination.resolve()
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination_resolved):
                raise RuntimeError(f"Unsafe path in upstream archive: {member.name}")
        tar.extractall(destination, filter="data")


def checkout_source(source: Path | None, temp: Path, tag: str) -> Path:
    if source is not None:
        repo = source.resolve()
        run("git", "rev-parse", "--git-dir", cwd=repo, capture=True)
        run("git", "fetch", "--depth=1", "origin", f"refs/tags/{tag}:refs/tags/{tag}", cwd=repo)
        return repo
    repo = temp / "repo"
    run("git", "clone", "--filter=blob:none", "--no-checkout", UPSTREAM, str(repo))
    run("git", "fetch", "--depth=1", "origin", f"refs/tags/{tag}:refs/tags/{tag}", cwd=repo)
    return repo


def import_release(tag: str, source: Path | None) -> dict[str, object]:
    if not TAG_PATTERN.fullmatch(tag):
        raise ValueError(f"Expected a stable SemVer tag, received {tag!r}")
    with tempfile.TemporaryDirectory(prefix="lakemeter-update-") as raw_temp:
        temp = Path(raw_temp)
        repo = checkout_source(source, temp, tag)
        commit = run("git", "rev-parse", f"{tag}^{{}}", cwd=repo, capture=True)

        full_archive = temp / "upstream.tar"
        with full_archive.open("wb") as output:
            subprocess.run(
                ["git", "archive", "--format=tar", commit],
                cwd=repo,
                check=True,
                stdout=output,
            )

        selected_archive = temp / "selected.tar"
        with selected_archive.open("wb") as output:
            subprocess.run(
                ["git", "archive", "--format=tar", commit, *INCLUDE],
                cwd=repo,
                check=True,
                stdout=output,
            )
        selected = temp / "selected"
        selected.mkdir()
        safe_extract(selected_archive, selected)

        required = (
            selected / "LICENSE.md",
            selected / "backend" / "app" / "routes" / "estimates.py",
            selected / "frontend" / "src" / "pages" / "Calculator.tsx",
            selected / "backend" / "static" / "pricing" / "manifest.json",
            selected / "scripts" / "install_lakemeter.py",
        )
        missing = [str(path.relative_to(selected)) for path in required if not path.is_file()]
        if missing:
            raise RuntimeError(f"Upstream compatibility inputs are missing: {missing}")

        pricing_manifest = json.loads(
            (selected / "backend" / "static" / "pricing" / "manifest.json").read_text()
        )
        lock = {
            "repository": "databrickslabs/lakemeter-oss",
            "tag": tag,
            "commit": commit,
            "archive_sha256": sha256(full_archive),
            "vendored_tree_sha256": tree_sha256(selected),
            "license_sha256": sha256(selected / "LICENSE.md"),
            "schema_version": 1,
            "pricing_version": pricing_manifest.get("generated_at", "unknown"),
            "included_paths": list(INCLUDE),
        }

        VENDOR.parent.mkdir(parents=True, exist_ok=True)
        if VENDOR.exists():
            shutil.rmtree(VENDOR)
        shutil.copytree(selected, VENDOR)
        if PRICING_ASSETS.exists():
            shutil.rmtree(PRICING_ASSETS)
        PRICING_ASSETS.mkdir(parents=True)
        for source_file in sorted(
            (selected / "backend" / "static" / "pricing").glob("*.csv")
        ):
            target_file = PRICING_ASSETS / f"{source_file.name}.gz"
            with source_file.open("rb") as source_handle, target_file.open("wb") as raw:
                with gzip.GzipFile(
                    filename=source_file.name,
                    mode="wb",
                    fileobj=raw,
                    mtime=0,
                ) as compressed:
                    shutil.copyfileobj(source_handle, compressed)
        shutil.copy2(
            selected / "backend" / "static" / "pricing" / "manifest.json",
            PRICING_ASSETS / "manifest.json",
        )
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        LOCK.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
        return lock


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    lock = import_release(args.tag, args.source)
    print(json.dumps(lock, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

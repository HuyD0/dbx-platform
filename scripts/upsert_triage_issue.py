"""Create or update the rolling platform-triage GitHub issue from check output.

Reads NDJSON files produced by ``dbx-platform ... --output json`` (one JSON
document per report block), and upserts a single issue labeled
``platform-triage`` whose body carries the findings for human review. The
script intentionally avoids tagging coding agents; remediation should start
from the in-app alert and a deliberate human request.

Uses the ``gh`` CLI (present on GitHub runners) with the workflow's
GITHUB_TOKEN. Stdlib only; no third-party dependencies.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

LABEL = "platform-triage"
TITLE = "Platform triage: automated check findings"


def load_blocks(paths: list[str]) -> list[dict]:
    blocks = []
    for p in paths:
        for line in Path(p).read_text().splitlines():
            line = line.strip()
            if line.startswith("{"):
                blocks.append(json.loads(line))
    return blocks


def build_body(blocks: list[dict]) -> str:
    sections = []
    for b in blocks:
        if not b.get("rows"):
            continue
        rows = json.dumps(b["rows"], indent=2, default=str)
        sections.append(f"### {b['title']} ({b['count']})\n\n```json\n{rows}\n```")
    findings = "\n\n".join(sections)
    return f"""Automated checks found actionable issues on {date.today().isoformat()}.

Review the findings below in the Platform Console, then ask an agent or
engineer to propose code changes only when you want remediation work to begin.
For example, policy drift is fixed by editing `policies/*.json`, and
right-sizing findings by adjusting job/cluster specs kept in git. Do not
attempt to change the workspace directly: `--apply` actions stay
human-invoked, per the repo safety model.

{findings}

---
*Refreshed weekly by `.github/workflows/platform-triage.yml`. Findings JSON
comes from `dbx-platform ... --output json`.*
"""


def gh(*args: str) -> str:
    return subprocess.run(
        ["gh", *args], check=True, capture_output=True, text=True
    ).stdout


def main(paths: list[str]) -> int:
    blocks = load_blocks(paths)
    total = sum(b.get("count", 0) for b in blocks)
    if total == 0:
        print("no findings — no issue needed")
        return 0
    body = build_body(blocks)
    existing = json.loads(
        gh("issue", "list", "--label", LABEL, "--state", "open", "--json", "number")
    )
    if existing:
        number = str(existing[0]["number"])
        gh("issue", "edit", number, "--body", body)
        print(f"updated issue #{number} ({total} findings)")
    else:
        gh("label", "create", LABEL, "--force",
           "--description", "Rolling automated platform triage")
        url = gh("issue", "create", "--title", TITLE, "--label", LABEL, "--body", body)
        print(f"created {url.strip()} ({total} findings)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

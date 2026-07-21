"""Pure formatting helpers shared by every platform-agent runtime.

No third-party imports — unit-tested offline like the package's decision
logic (tests/test_agent.py loads this file directly).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

_IDENTITY_FIELDS = {
    "created_by",
    "creator",
    "email",
    "owner",
    "principal",
    "requester",
    "run_as",
    "user",
    "user_name",
    "username",
}


def _stable_mask(value, kind: str) -> str:
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:8]
    return f"{kind}-{digest}"


def _display_value(key: str, value):
    normalized = key.lower()
    if normalized == "token_id":
        return _stable_mask(value, "token")
    if normalized in _IDENTITY_FIELDS or normalized.endswith(("_email", "_owner")):
        return _stable_mask(value, "identity")
    if normalized == "comment":
        return "[redacted]"
    return value


def rows_to_text(
    rows: list[dict],
    limit: int = 50,
    *,
    tool_name: str = "unspecified",
    source: str = "unspecified",
    observed_at: str | None = None,
) -> str:
    """Render compact evidence plus a machine-readable citation footer."""
    timestamp = observed_at or datetime.now(UTC).isoformat()
    footer = (
        "\nEVIDENCE:"
        f"tool={tool_name};source={source};observed_at={timestamp}"
    )
    if not rows:
        return "No findings — nothing to report." + footer
    shown = rows[:limit]
    lines = []
    for r in shown:
        pairs = ", ".join(
            f"{k}={_display_value(str(k), v)}"
            for k, v in r.items()
            if not str(k).startswith("_") and v not in ("", None)
        )
        lines.append(f"- {pairs}")
    if len(rows) > limit:
        lines.append(f"... and {len(rows) - limit} more rows (total {len(rows)}).")
    return "\n".join(lines) + footer


SYSTEM_PROMPT = """\
You are the dbx-platform assistant, a read-only advisor for a Databricks \
workspace. You can inspect cost, security, housekeeping, governance and \
AI/ML checks via your tools — the same checks the platform team's CLI, \
bundle jobs and Platform Console run. The jobs ship with paused schedules: \
every run is human-initiated, so proposing runs is a core part of your job.

Rules:
- You cannot change anything, and you must never claim to have done so. \
Every remediation is a structured recommendation that must be planned and \
approved through Mission Control. Direct CLI apply paths do not exist.
- To get something done for a user chatting in the Platform Console, use the \
propose_remediation or propose_job_run tool (both are dry-runs) and copy the \
resulting ACTION_PROPOSAL:/JOB_PROPOSAL: marker line verbatim, on its own \
line, into your final answer — the console turns it into an immutable plan \
for authorized human approval. Never fabricate a marker line yourself.
- Cite the tool name, underlying table or resource named by that tool, and \
the observation time for factual claims. Never invent numbers or citations.
- When findings are empty, say so plainly.
"""

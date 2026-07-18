"""Pure formatting helpers for the platform agent's tools.

No third-party imports — unit-tested offline like the package's decision
logic (tests/test_agent.py loads this file directly).
"""

from __future__ import annotations


def rows_to_text(rows: list[dict], limit: int = 50) -> str:
    """Render finding/report rows as compact text for the model."""
    if not rows:
        return "No findings — nothing to report."
    shown = rows[:limit]
    lines = []
    for r in shown:
        pairs = ", ".join(
            f"{k}={v}" for k, v in r.items() if not str(k).startswith("_") and v not in ("", None)
        )
        lines.append(f"- {pairs}")
    if len(rows) > limit:
        lines.append(f"... and {len(rows) - limit} more rows (total {len(rows)}).")
    return "\n".join(lines)


SYSTEM_PROMPT = """\
You are the dbx-platform assistant, a read-only advisor for a Databricks \
workspace. You can inspect cost, security, housekeeping, governance and \
AI/ML checks via your tools — the same checks the platform team's CLI, \
bundle jobs and Platform Console run. The jobs ship with paused schedules: \
every run is human-initiated, so proposing runs is a core part of your job.

Rules:
- You cannot change anything, and you must never claim to have done so. \
Every remediation is a recommendation for a human, naming the CLI command \
(e.g. `dbx-platform housekeeping stale-clusters --apply --yes`) or the git \
change (e.g. edit policies/*.json) that a human would run or review.
- To get something done for a user chatting in the Platform Console, use the \
propose_remediation, propose_job_run, or propose_run_all_jobs tool (all are \
dry-runs) and copy the resulting ACTION_PROPOSAL:/JOB_PROPOSAL: marker line \
verbatim, on its own line, into your final answer — the console turns it \
into a confirmation card. When the user asks to run all the jobs (or run \
everything), use propose_run_all_jobs. Never fabricate a marker line \
yourself; only relay one produced by a tool, and remind the user the change \
happens only after they confirm.
- Cite concrete figures from tool output; never invent numbers.
- When findings are empty, say so plainly.
"""

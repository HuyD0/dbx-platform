"""Parse agent proposal markers out of chat text. Pure — unit-tested offline.

The platform agent's proposal tools end their output with a marker line
(ACTION_PROPOSAL:{...} or JOB_PROPOSAL:{...}) that the agent copies verbatim
into its final answer. The console strips the markers from the displayed
markdown and returns them as structured proposals the UI renders as
confirm-gated cards. The agent's numbers are never trusted for the apply —
the card triggers a fresh server-side dry-run.
"""

from __future__ import annotations

import json
import re

_MARKER_RE = re.compile(r"^(ACTION_PROPOSAL|JOB_PROPOSAL):(\{.*\})[ \t]*$", re.MULTILINE)

_KIND = {"ACTION_PROPOSAL": "action", "JOB_PROPOSAL": "job"}


def parse_proposals(text: str) -> tuple[str, list[dict]]:
    """Return (text with marker lines removed, structured proposals)."""
    proposals: list[dict] = []

    def _extract(m: re.Match) -> str:
        try:
            data = json.loads(m.group(2))
        except json.JSONDecodeError:
            return m.group(0)  # malformed marker: leave it visible rather than drop it
        if not isinstance(data, dict):
            return m.group(0)
        proposals.append({"kind": _KIND[m.group(1)], **data})
        return ""

    clean = _MARKER_RE.sub(_extract, text)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, proposals

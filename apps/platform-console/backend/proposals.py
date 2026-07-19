"""Parse agent proposal markers out of chat text. Pure — unit-tested offline.

The platform agent's proposal tools end their output with a marker line
(ACTION_PROPOSAL:{...} or JOB_PROPOSAL:{...}) that the agent copies verbatim
into its final answer. The console strips markers from displayed markdown and
returns structured proposals that the UI turns into immutable, exact-target
plans. Legacy batch markers remain parseable but are display-only; there is no
batch execution endpoint.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

_MARKER_RE = re.compile(r"^(ACTION_PROPOSAL|JOB_PROPOSAL):(\{.*\})[ \t]*$", re.MULTILINE)

_KIND = {"ACTION_PROPOSAL": "action", "JOB_PROPOSAL": "job"}
_EVIDENCE_RE = re.compile(r"^EVIDENCE:(.+?)[ \t]*$", re.MULTILINE)
_EVIDENCE_REQUIRED = {"tool", "source", "observed_at"}
_EVIDENCE_ALLOWED = _EVIDENCE_REQUIRED | {"resource", "finding_id"}


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


def parse_evidence_citations(text: str) -> tuple[str, list[dict]]:
    """Return display text plus deduplicated structured evidence markers.

    Tools emit a deliberately small marker format that the model is instructed
    to preserve verbatim. Invalid markers stay visible so the UI never hides a
    malformed or unsupported citation.
    """
    citations: list[dict] = []
    seen: set[tuple[str, ...]] = set()

    def _extract(match: re.Match) -> str:
        fields: dict[str, str] = {}
        for segment in match.group(1).split(";"):
            key, separator, value = segment.partition("=")
            key = key.strip()
            value = value.strip()
            if (
                not separator
                or not key
                or not value
                or key not in _EVIDENCE_ALLOWED
                or key in fields
            ):
                return match.group(0)
            fields[key] = value
        if not _EVIDENCE_REQUIRED.issubset(fields):
            return match.group(0)
        try:
            observed_at = datetime.fromisoformat(
                fields["observed_at"].replace("Z", "+00:00")
            )
        except ValueError:
            return match.group(0)
        if observed_at.tzinfo is None:
            return match.group(0)
        if (
            len(fields["tool"]) > 100
            or len(fields["source"]) > 300
            or any(len(value) > 500 for value in fields.values())
        ):
            return match.group(0)
        identity = tuple(fields.get(key, "") for key in sorted(_EVIDENCE_ALLOWED))
        if identity not in seen:
            seen.add(identity)
            digest = hashlib.sha256("\x1f".join(identity).encode()).hexdigest()[:16]
            citations.append({"citation_id": f"evidence-{digest}", **fields})
        return ""

    clean = _EVIDENCE_RE.sub(_extract, text)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, citations

#!/usr/bin/env bash
# Stop: final offline-test gate. Runs the suite when Claude finishes and warns
# (non-blocking) if it's red. Skips silently when pytest or the package aren't
# available (fresh clone, no editable install) so it never nags with false
# failures. Make it blocking by emitting {"decision":"block","reason":...} if you
# want Claude to keep fixing until green.
[ -n "${CLAUDE_PROJECT_DIR:-}" ] && cd "$CLAUDE_PROJECT_DIR" 2>/dev/null
command -v pytest >/dev/null 2>&1 || exit 0
python -c "import dbx_platform" >/dev/null 2>&1 || exit 0
if out=$(pytest -q 2>&1); then
  exit 0
fi
tail=$(printf '%s' "$out" | tail -3 | tr '\n' ' ')
jq -n --arg m "⚠ pytest is failing after this turn: $tail" '{systemMessage:$m}'

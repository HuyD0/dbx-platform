#!/usr/bin/env bash
# PreToolUse(Edit|Write|MultiEdit): warn (non-blocking) when editing a GENERATED
# dashboard. `dashboards/*.lvdash.json` is rendered from `dashboards/templates/`
# by `dbx-platform dashboards render` — hand edits there get overwritten.
input=$(cat)
f=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')
case "$f" in
  */dashboards/templates/*) exit 0 ;;   # the source — editing here is correct
  *.lvdash.json)
    jq -n '{systemMessage:"⚠ dashboards/*.lvdash.json is GENERATED. Edit the source under dashboards/templates/ and re-render with `dbx-platform dashboards render`, or this change will be overwritten."}'
    ;;
esac
exit 0

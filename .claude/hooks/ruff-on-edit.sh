#!/usr/bin/env bash
# PostToolUse(Edit|Write|MultiEdit): lint an edited Python file with ruff and,
# if it flags anything, feed the findings back to the model as context. Silent
# and non-blocking on success or when ruff isn't installed.
input=$(cat)
f=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')
[ -z "$f" ] && exit 0
case "$f" in *.py) : ;; *) exit 0 ;; esac
command -v ruff >/dev/null 2>&1 || exit 0
if out=$(ruff check "$f" 2>&1); then
  exit 0
fi
jq -n --arg c "ruff flagged $f (fix before finishing):
$out" \
  '{hookSpecificOutput:{hookEventName:"PostToolUse",additionalContext:$c}}'

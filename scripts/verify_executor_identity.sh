#!/usr/bin/env bash
set -euo pipefail

runtime="${BUNDLE_VAR_runtime_executor_service_principal_name:-}"
action="${BUNDLE_VAR_action_executor_service_principal_name:-}"
actions_enabled="${BUNDLE_VAR_actions_enabled:-false}"
allow_shared="${DBX_PLATFORM_ALLOW_SHARED_EXECUTOR_SP:-false}"

if [[ -z "$runtime" || -z "$action" ]]; then
  echo "Both executor service-principal variables are required." >&2
  exit 1
fi

if [[ "$runtime" == "$action" ]]; then
  if [[ "$allow_shared" != "true" || "$actions_enabled" != "false" ]]; then
    echo "Shared executor identity is allowed only by an explicit proposal-only bootstrap exception." >&2
    exit 1
  fi
  echo "::warning::Using one executor identity temporarily; actions remain disabled."
fi

"""WorkspaceClient factory.

No workspace URL appears anywhere in this package. Resolution order:

1. Explicit ``--profile`` flag (a named profile in ~/.databrickscfg).
2. Databricks unified auth: DATABRICKS_HOST / DATABRICKS_CONFIG_PROFILE /
   OAuth env vars, then the DEFAULT profile in ~/.databrickscfg.
3. Native runtime auth when running inside a Databricks job or notebook —
   WorkspaceClient() picks up the runtime's credentials automatically.

This is what lets the exact same code run ad-hoc from a laptop and as a
scheduled bundle-deployed job with zero branching.
"""

from __future__ import annotations

from databricks.sdk import WorkspaceClient


def get_client(profile: str | None = None) -> WorkspaceClient:
    if profile:
        return WorkspaceClient(profile=profile)
    return WorkspaceClient()

"""MLflow prompt-registry lineage for the estimator's extraction prompts.

The wheel-shipped prompt texts (``estimator_data/prompts/``) are the runtime
source of truth — the app never fetches a prompt remotely. This module gives
them an auditable registry identity: the deployment-run ``estimator_prompt_sync``
job registers each text to the Unity Catalog prompt registry, tagged with its
content hash, and skips registration when the hash already matches the latest
version. Extraction traces carry the same hashes, so a trace, a registry
version and a git commit all point at identical bytes.

The orchestration (``sync_prompts``) is pure and takes callables, so the
skip/register decision is unit-tested offline; only ``register_prompts`` wires
real MLflow (lazy import — mlflow ships in the job environment, not the wheel).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from dbx_platform import estimator

PROMPT_NAMES = ("estimator_pattern_classify", "estimator_requirements_extract")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def prompt_specs(catalog: str, schema: str) -> list[dict]:
    """Registry name + wheel text + content hash per prompt. Pure."""

    return [
        {
            "prompt": name,
            "registry_name": f"{catalog}.{schema}.{name}",
            "text": estimator.load_prompt(name),
            "content_hash": content_hash(estimator.load_prompt(name)),
        }
        for name in PROMPT_NAMES
    ]


def sync_prompts(
    specs: list[dict],
    *,
    latest_hash: Callable[[str], str | None],
    register: Callable[[str, str, str], object],
) -> list[dict]:
    """Register each prompt whose content hash changed. Pure orchestration.

    ``latest_hash(registry_name)`` returns the latest registered version's
    content-hash tag (None when the prompt has never been registered);
    ``register(registry_name, text, content_hash)`` performs the registration.
    """
    results = []
    for spec in specs:
        current = latest_hash(spec["registry_name"])
        if current == spec["content_hash"]:
            results.append(
                {**{k: spec[k] for k in ("prompt", "registry_name", "content_hash")},
                 "action": "unchanged"}
            )
            continue
        register(spec["registry_name"], spec["text"], spec["content_hash"])
        results.append(
            {**{k: spec[k] for k in ("prompt", "registry_name", "content_hash")},
             "action": "registered" if current is None else "updated",
             "previous_hash": current}
        )
    return results


def register_prompts(catalog: str, schema: str) -> list[dict]:
    """Sync the wheel prompts to the UC prompt registry (needs mlflow>=3)."""

    import mlflow
    import mlflow.genai

    mlflow.set_registry_uri("databricks-uc")

    def latest_hash(registry_name: str) -> str | None:
        try:
            prompt = mlflow.genai.load_prompt(f"prompts:/{registry_name}@latest")
        except Exception:  # noqa: BLE001 - first registration or alias absent
            return None
        return (getattr(prompt, "tags", None) or {}).get("content_hash")

    def register(registry_name: str, text: str, digest: str) -> None:
        mlflow.genai.register_prompt(
            name=registry_name,
            template=text,
            commit_message=f"dbx-platform deploy sync (content {digest})",
            tags={"content_hash": digest, "source": "dbx-platform wheel"},
        )

    return sync_prompts(
        prompt_specs(catalog, schema), latest_hash=latest_hash, register=register
    )

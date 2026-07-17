"""Governance: cluster policies as code and tag compliance.

policies/*.json in the repo is the source of truth. The diff never deletes
workspace policies that aren't in git — they're reported as "unmanaged".
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from databricks.sdk import WorkspaceClient

from dbx_platform.system_tables import load_query, run_query

# --- cluster policies as code ----------------------------------------------

def load_local_policies(policies_dir: str | Path) -> list[dict]:
    """Each file: {"name": str, "definition": dict, "description"?: str,
    "max_clusters_per_user"?: int}."""
    policies = []
    for path in sorted(Path(policies_dir).glob("*.json")):
        data = json.loads(path.read_text())
        if "name" not in data or "definition" not in data:
            raise ValueError(f"{path}: policy file must contain 'name' and 'definition'")
        data["_file"] = path.name
        policies.append(data)
    return policies


def fetch_remote_policies(w: WorkspaceClient) -> list[dict]:
    out = []
    for p in w.cluster_policies.list():
        out.append(
            {
                "policy_id": p.policy_id,
                "name": p.name or "",
                "definition": p.definition or "{}",
                "description": p.description or "",
                "max_clusters_per_user": p.max_clusters_per_user,
            }
        )
    return out


def _normalize(definition: str | dict) -> dict:
    return json.loads(definition) if isinstance(definition, str) else definition


def diff_policies(local: list[dict], remote: list[dict]) -> dict[str, list[dict]]:
    """Pure decision logic. Compares parsed JSON (key order/whitespace
    insensitive). Returns {create, update, unchanged, unmanaged}."""
    remote_by_name = {r["name"]: r for r in remote}
    plan: dict[str, list[dict]] = {"create": [], "update": [], "unchanged": [], "unmanaged": []}
    for lp in local:
        rp = remote_by_name.get(lp["name"])
        if rp is None:
            plan["create"].append(lp)
            continue
        same_def = _normalize(lp["definition"]) == _normalize(rp["definition"])
        same_max = (
            "max_clusters_per_user" not in lp
            or lp.get("max_clusters_per_user") == rp.get("max_clusters_per_user")
        )
        if same_def and same_max:
            plan["unchanged"].append(lp)
        else:
            plan["update"].append({**lp, "policy_id": rp["policy_id"]})
    managed_names = {lp["name"] for lp in local}
    plan["unmanaged"] = [r for r in remote if r["name"] not in managed_names]
    return plan


def apply_policy_plan(w: WorkspaceClient, plan: dict[str, list[dict]]) -> list[str]:
    done = []
    for p in plan["create"]:
        w.cluster_policies.create(
            name=p["name"],
            definition=json.dumps(_normalize(p["definition"])),
            description=p.get("description"),
            max_clusters_per_user=p.get("max_clusters_per_user"),
        )
        done.append(f"created policy '{p['name']}'")
    for p in plan["update"]:
        w.cluster_policies.edit(
            policy_id=p["policy_id"],
            name=p["name"],
            definition=json.dumps(_normalize(p["definition"])),
            description=p.get("description"),
            max_clusters_per_user=p.get("max_clusters_per_user"),
        )
        done.append(f"updated policy '{p['name']}'")
    return done


# --- tag compliance ---------------------------------------------------------

def fetch_taggable_resources(w: WorkspaceClient) -> list[dict]:
    resources = []
    for c in w.clusters.list():
        if c.cluster_source and c.cluster_source.value == "JOB":
            continue
        resources.append(
            {
                "type": "cluster",
                "id": c.cluster_id or "",
                "name": c.cluster_name or "",
                "tags": dict(c.custom_tags or {}),
                "creator": c.creator_user_name or "",
            }
        )
    for j in w.jobs.list():
        tags = dict(j.settings.tags or {}) if j.settings else {}
        name = j.settings.name if j.settings else ""
        resources.append(
            {
                "type": "job",
                "id": str(j.job_id),
                "name": name or "",
                "tags": tags,
                "creator": j.creator_user_name or "",
            }
        )
    return resources


def find_missing_tags(resources: list[dict], required_tags: list[str]) -> list[dict]:
    """Pure decision logic: resources missing any required tag key."""
    findings = []
    for r in resources:
        missing = [t for t in required_tags if t not in r["tags"]]
        if missing:
            findings.append(
                {"type": r["type"], "id": r["id"], "name": r["name"],
                 "missing_tags": ", ".join(missing)}
            )
    return findings


# --- tag recommendations ----------------------------------------------------

def _norm(key: str) -> str:
    """Fold a tag key to a comparison form: lowercase, separators removed, so
    ``Cost-Center``/``cost_center``/``costcenter`` all collapse to one form."""
    return re.sub(r"[\s_-]+", "", key.lower())


def _name_tokens(name: str) -> set[str]:
    """Lowercased tokens of a resource name, split on separators and camelCase
    boundaries (``atlas-nightly`` -> {atlas, nightly}, ``AtlasEtl`` -> {atlas, etl})."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return {t for t in re.split(r"[\s_.\-/]+", spaced.lower()) if t}


def recommend_tags(
    resources: list[dict],
    required_tags: list[str],
    *,
    min_ratio: float = 0.8,
    owner_keys: tuple[str, ...] = ("owner", "email", "contact"),
) -> list[dict]:
    """Pure decision logic: for each resource missing a required tag key, suggest
    a fix. One row per (resource, missing key) that has a signal; keys with no
    signal are skipped (those are already covered by ``find_missing_tags``).

    Strategies, applied in priority order per missing key:

    - **rename** (high) — the value is already present under a mistyped or
      differently-formatted key. Matched by normalized key equality, else a
      ``difflib`` ratio ``>= min_ratio``.
    - **vocabulary** (medium) — a value already used for this key elsewhere in
      the workspace appears as a token in this resource's name.
    - **owner** (medium) — for ownership-type keys, use the resource creator.

    Report-only: suggestions are advice, never applied here.
    """
    owner_norm = {_norm(k) for k in owner_keys}
    # Value vocabulary already in use for each required key across all resources.
    vocab: dict[str, set[str]] = {req: set() for req in required_tags}
    for r in resources:
        for req in required_tags:
            val = r["tags"].get(req)
            if val:
                vocab[req].add(val)

    findings = []
    for r in resources:
        tags = r["tags"]
        creator = r.get("creator", "")
        tokens = _name_tokens(r.get("name", ""))
        for req in required_tags:
            if req in tags:
                continue
            req_norm = _norm(req)

            # A — rename a near-match key (best ratio wins).
            best_key, best_ratio = None, 0.0
            for present in tags:
                if _norm(present) == req_norm:
                    best_key, best_ratio = present, 1.0
                    break
                ratio = SequenceMatcher(None, _norm(present), req_norm).ratio()
                if ratio > best_ratio:
                    best_key, best_ratio = present, ratio
            if best_key is not None and best_ratio >= min_ratio:
                findings.append(_rec(r, req, f"rename key '{best_key}' -> '{req}'",
                                     "high", "near-match-key"))
                continue

            # B — a known value for this key appears in the resource name.
            match = sorted(v for v in vocab[req] if v.lower() in tokens)
            if match:
                findings.append(_rec(r, req, f"set {req}={match[0]}",
                                     "medium", "name-matches-known-value"))
                continue

            # C — infer an ownership-type key from the creator.
            if req_norm in owner_norm and creator:
                findings.append(_rec(r, req, f"set {req}={creator}",
                                     "medium", "creator"))
    return findings


def _rec(r: dict, missing_tag: str, suggestion: str, confidence: str, basis: str) -> dict:
    return {
        "type": r["type"],
        "id": r["id"],
        "name": r["name"],
        "missing_tag": missing_tag,
        "suggestion": suggestion,
        "confidence": confidence,
        "basis": basis,
    }


def untagged_spend(w: WorkspaceClient, warehouse_id: str, days: int) -> list[dict]:
    return run_query(w, load_query("untagged_usage"), warehouse_id, {"days": days})

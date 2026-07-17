"""Governance: cluster policies as code and tag compliance.

policies/*.json in the repo is the source of truth. The diff never deletes
workspace policies that aren't in git — they're reported as "unmanaged".
"""

from __future__ import annotations

import json
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
            }
        )
    for j in w.jobs.list():
        tags = dict(j.settings.tags or {}) if j.settings else {}
        name = j.settings.name if j.settings else ""
        resources.append({"type": "job", "id": str(j.job_id), "name": name or "", "tags": tags})
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


def untagged_spend(w: WorkspaceClient, warehouse_id: str, days: int) -> list[dict]:
    return run_query(w, load_query("untagged_usage"), warehouse_id, {"days": days})

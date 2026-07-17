import json

from dbx_platform.governance import diff_policies, find_missing_tags

LOCAL = {
    "name": "autotermination-required",
    "definition": {"autotermination_minutes": {"type": "range", "maxValue": 60}},
}


def _remote(definition, name="autotermination-required", policy_id="p-1", **kw) -> dict:
    return {"policy_id": policy_id, "name": name,
            "definition": json.dumps(definition), "description": "", **kw}


def test_semantically_equal_json_is_unchanged():
    # same definition, different key order inside the nested object
    remote = _remote({"autotermination_minutes": {"maxValue": 60, "type": "range"}})
    plan = diff_policies([LOCAL], [remote])
    assert [p["name"] for p in plan["unchanged"]] == ["autotermination-required"]
    assert plan["create"] == [] and plan["update"] == []


def test_missing_remote_policy_is_created():
    plan = diff_policies([LOCAL], [])
    assert [p["name"] for p in plan["create"]] == ["autotermination-required"]


def test_changed_definition_is_updated():
    remote = _remote({"autotermination_minutes": {"type": "range", "maxValue": 120}})
    plan = diff_policies([LOCAL], [remote])
    assert [p["name"] for p in plan["update"]] == ["autotermination-required"]
    assert plan["update"][0]["policy_id"] == "p-1"


def test_workspace_only_policy_is_unmanaged_never_deleted():
    remote = _remote({"spark_version": {"type": "fixed", "value": "x"}}, name="hand-made")
    plan = diff_policies([LOCAL], [remote])
    assert [p["name"] for p in plan["unmanaged"]] == ["hand-made"]
    assert "delete" not in plan


def test_max_clusters_per_user_difference_triggers_update():
    local = {**LOCAL, "max_clusters_per_user": 2}
    remote = _remote({"autotermination_minutes": {"type": "range", "maxValue": 60}},
                     max_clusters_per_user=5)
    plan = diff_policies([local], [remote])
    assert len(plan["update"]) == 1


def test_find_missing_tags():
    resources = [
        {"type": "cluster", "id": "c-1", "name": "ok",
         "tags": {"team": "data", "project": "x"}},
        {"type": "job", "id": "42", "name": "partial", "tags": {"team": "data"}},
        {"type": "cluster", "id": "c-2", "name": "none", "tags": {}},
    ]
    findings = find_missing_tags(resources, ["team", "project"])
    assert {(f["id"], f["missing_tags"]) for f in findings} == {
        ("42", "project"),
        ("c-2", "team, project"),
    }

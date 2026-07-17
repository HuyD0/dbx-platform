import json

from dbx_platform.governance import diff_policies, find_missing_tags, recommend_tags

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


def _rec_by_id(findings, resource_id):
    return next(f for f in findings if f["id"] == resource_id)


def test_recommend_rename_near_match_key():
    # Value is present under a mistyped/differently-formatted key.
    resources = [
        {"type": "cluster", "id": "c-1", "name": "etl",
         "tags": {"costcenter": "cc-42"}, "creator": "a@corp.com"},
    ]
    recs = recommend_tags(resources, ["cost_center"])
    rec = _rec_by_id(recs, "c-1")
    assert rec["missing_tag"] == "cost_center"
    assert rec["confidence"] == "high"
    assert rec["basis"] == "near-match-key"
    assert "costcenter" in rec["suggestion"] and "cost_center" in rec["suggestion"]


def test_recommend_value_from_vocabulary():
    # One resource already uses project=atlas; another named atlas-nightly is
    # missing project -> recommend that known value.
    resources = [
        {"type": "cluster", "id": "c-1", "name": "tagged",
         "tags": {"project": "atlas"}, "creator": ""},
        {"type": "cluster", "id": "c-2", "name": "atlas-nightly",
         "tags": {}, "creator": ""},
    ]
    recs = recommend_tags(resources, ["project"])
    rec = _rec_by_id(recs, "c-2")
    assert rec["suggestion"] == "set project=atlas"
    assert rec["confidence"] == "medium"
    assert rec["basis"] == "name-matches-known-value"


def test_recommend_owner_from_creator():
    resources = [
        {"type": "job", "id": "7", "name": "nightly",
         "tags": {}, "creator": "a@corp.com"},
    ]
    recs = recommend_tags(resources, ["owner"])
    rec = _rec_by_id(recs, "7")
    assert rec["suggestion"] == "set owner=a@corp.com"
    assert rec["basis"] == "creator"


def test_recommend_respects_min_ratio_boundary():
    # 'product' vs 'project' normalizes to a difflib ratio of ~0.71 — below the
    # default 0.8 floor, so no rename is suggested; lowering the floor surfaces it.
    resources = [
        {"type": "cluster", "id": "c-1", "name": "shipping",
         "tags": {"product": "widgets"}, "creator": ""},
    ]
    assert recommend_tags(resources, ["project"]) == []
    recs = recommend_tags(resources, ["project"], min_ratio=0.7)
    assert _rec_by_id(recs, "c-1")["basis"] == "near-match-key"


def test_recommend_skips_when_no_signal():
    # Missing key with no near-match key, no known value, and not an owner key.
    resources = [
        {"type": "cluster", "id": "c-1", "name": "anon",
         "tags": {"foo": "bar"}, "creator": "a@corp.com"},
    ]
    assert recommend_tags(resources, ["project"]) == []

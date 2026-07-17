from conftest import days_ago, hours_ago

from dbx_platform.ml import (
    classify_gpu_clusters,
    classify_models,
    classify_serving_endpoints,
    find_stale_endpoints,
    find_vector_search_findings,
    served_entity_names,
)


def _entity(**overrides) -> dict:
    base = {
        "entity_name": "models.prod.churn",
        "workload_size": "Small",
        "workload_type": "CPU",
        "scale_to_zero": True,
        "is_external_or_fm": False,
    }
    return {**base, **overrides}


def _endpoint(**overrides) -> dict:
    base = {
        "name": "churn-endpoint",
        "creator": "someone@example.com",
        "task": "",
        "ready": "READY",
        "config_update": "NOT_UPDATING",
        "created_ms": days_ago(10),
        "updated_ms": days_ago(1),
        "served_entities": [_entity()],
        "is_external_or_fm": False,
        "has_inference_table": True,
        "has_rate_limits": False,
        "has_usage_tracking": False,
    }
    return {**base, **overrides}


# --- classify_serving_endpoints ------------------------------------------------

def test_healthy_endpoint_has_no_findings(now_ms):
    assert classify_serving_endpoints([_endpoint()], now_ms, failed_grace_hours=24) == []


def test_small_cpu_without_scale_to_zero_flagged(now_ms):
    e = _endpoint(served_entities=[_entity(scale_to_zero=False)])
    findings = classify_serving_endpoints([e], now_ms, failed_grace_hours=24)
    assert [f["action"] for f in findings] == ["enable-scale-to-zero (manual)"]


def test_gpu_workload_without_scale_to_zero_not_flagged(now_ms):
    e = _endpoint(served_entities=[_entity(scale_to_zero=False, workload_type="GPU_SMALL")])
    assert classify_serving_endpoints([e], now_ms, failed_grace_hours=24) == []


def test_not_ready_past_grace_flagged(now_ms):
    e = _endpoint(ready="NOT_READY", created_ms=hours_ago(48))
    findings = classify_serving_endpoints([e], now_ms, failed_grace_hours=24)
    assert [f["action"] for f in findings] == ["review-failed-endpoint"]


def test_not_ready_within_grace_not_flagged(now_ms):
    e = _endpoint(ready="NOT_READY", created_ms=hours_ago(2))
    assert classify_serving_endpoints([e], now_ms, failed_grace_hours=24) == []


def test_not_ready_at_exact_grace_boundary_flagged(now_ms):
    e = _endpoint(ready="NOT_READY", created_ms=hours_ago(24))
    findings = classify_serving_endpoints([e], now_ms, failed_grace_hours=24)
    assert len(findings) == 1


def test_update_failed_flagged(now_ms):
    e = _endpoint(config_update="UPDATE_FAILED", created_ms=hours_ago(48))
    findings = classify_serving_endpoints([e], now_ms, failed_grace_hours=24)
    assert findings[0]["reason"].startswith("endpoint UPDATE_FAILED")


def test_missing_inference_table_flagged(now_ms):
    e = _endpoint(has_inference_table=False)
    findings = classify_serving_endpoints([e], now_ms, failed_grace_hours=24)
    assert [f["action"] for f in findings] == ["enable-inference-table"]


def test_external_model_without_gateway_flagged_twice(now_ms):
    e = _endpoint(
        is_external_or_fm=True,
        served_entities=[_entity(is_external_or_fm=True, workload_size="")],
    )
    findings = classify_serving_endpoints([e], now_ms, failed_grace_hours=24)
    assert sorted(f["action"] for f in findings) == [
        "add-ai-gateway-rate-limits",
        "enable-usage-tracking",
    ]


def test_external_model_with_gateway_not_flagged(now_ms):
    e = _endpoint(
        is_external_or_fm=True,
        served_entities=[_entity(is_external_or_fm=True, workload_size="")],
        has_rate_limits=True,
        has_usage_tracking=True,
    )
    assert classify_serving_endpoints([e], now_ms, failed_grace_hours=24) == []


# --- find_stale_endpoints ------------------------------------------------------

def test_old_endpoint_with_no_usage_is_stale(now_ms):
    stale = find_stale_endpoints([_endpoint(created_ms=days_ago(45))], [], now_ms, stale_days=30)
    assert [s["name"] for s in stale] == ["churn-endpoint"]


def test_endpoint_with_usage_not_stale(now_ms):
    usage = [{"endpoint_name": "churn-endpoint", "requests": 5}]
    e = _endpoint(created_ms=days_ago(45))
    assert find_stale_endpoints([e], usage, now_ms, stale_days=30) == []


def test_young_endpoint_without_usage_not_stale(now_ms):
    e = _endpoint(created_ms=days_ago(3))
    assert find_stale_endpoints([e], [], now_ms, stale_days=30) == []


# --- classify_models -----------------------------------------------------------

def _model(**overrides) -> dict:
    base = {
        "full_name": "main.prod.churn",
        "owner": "someone@example.com",
        "created_ms": days_ago(20),
        "updated_ms": days_ago(5),
        "aliases": ["champion"],
        "versions": [{"version": 1, "created_ms": days_ago(5)}],
    }
    return {**base, **overrides}


SERVED = {"main.prod.churn"}


def _actions(findings: list[dict]) -> list[str]:
    return sorted(f["action"] for f in findings)


def test_healthy_served_model_has_no_findings(now_ms):
    assert classify_models([_model()], SERVED, now_ms, 90, 30) == []


def test_model_without_versions_flagged(now_ms):
    m = _model(versions=[], aliases=[])
    assert "delete-or-populate (manual)" in _actions(
        classify_models([m], SERVED, now_ms, 90, 30)
    )


def test_model_without_owner_flagged(now_ms):
    m = _model(owner="")
    assert "assign-owner" in _actions(classify_models([m], SERVED, now_ms, 90, 30))


def test_stale_model_flagged_at_boundary(now_ms):
    m = _model(updated_ms=days_ago(90))
    assert "archive-candidate" in _actions(classify_models([m], SERVED, now_ms, 90, 30))


def test_recently_updated_model_not_stale(now_ms):
    m = _model(updated_ms=days_ago(89.5))
    assert "archive-candidate" not in _actions(classify_models([m], SERVED, now_ms, 90, 30))


def test_old_unaliased_versions_flagged(now_ms):
    m = _model(aliases=[], versions=[{"version": 1, "created_ms": days_ago(31)}])
    assert "set-champion-alias" in _actions(classify_models([m], SERVED, now_ms, 90, 30))


def test_fresh_unaliased_versions_not_flagged(now_ms):
    m = _model(aliases=[], versions=[{"version": 1, "created_ms": days_ago(2)}])
    assert "set-champion-alias" not in _actions(classify_models([m], SERVED, now_ms, 90, 30))


def test_never_served_model_flagged_as_info(now_ms):
    findings = classify_models([_model()], set(), now_ms, 90, 30)
    assert _actions(findings) == ["never-served (info)"]


def test_served_entity_names_collects_from_endpoints():
    endpoints = [_endpoint(served_entities=[_entity(entity_name="main.prod.churn")])]
    assert served_entity_names(endpoints) == {"main.prod.churn"}


# --- classify_gpu_clusters -----------------------------------------------------

GPU_TYPES = {"Standard_NC6s_v3", "Standard_NC24ads_A100_v4"}


def _gpu_cluster(**overrides) -> dict:
    base = {
        "cluster_id": "c-gpu",
        "cluster_name": "training",
        "state": "RUNNING",
        "source": "UI",
        "node_type_id": "Standard_NC6s_v3",
        "driver_node_type_id": "Standard_DS3_v2",
        "start_time": hours_ago(2),
        "autotermination_minutes": 60,
        "creator": "someone@example.com",
    }
    return {**base, **overrides}


def test_gpu_cluster_within_limits_not_flagged(now_ms):
    assert classify_gpu_clusters([_gpu_cluster()], GPU_TYPES, now_ms, 8) == []


def test_non_gpu_cluster_ignored(now_ms):
    c = _gpu_cluster(node_type_id="Standard_DS3_v2", autotermination_minutes=0)
    assert classify_gpu_clusters([c], GPU_TYPES, now_ms, 8) == []


def test_job_source_gpu_cluster_ignored(now_ms):
    c = _gpu_cluster(source="JOB", autotermination_minutes=0)
    assert classify_gpu_clusters([c], GPU_TYPES, now_ms, 8) == []


def test_gpu_cluster_without_autotermination_flagged(now_ms):
    findings = classify_gpu_clusters(
        [_gpu_cluster(autotermination_minutes=0)], GPU_TYPES, now_ms, 8
    )
    assert [f["action"] for f in findings] == ["terminate (manual)"]


def test_gpu_cluster_over_uptime_boundary_flagged(now_ms):
    findings = classify_gpu_clusters(
        [_gpu_cluster(start_time=hours_ago(8))], GPU_TYPES, now_ms, 8
    )
    assert len(findings) == 1


def test_gpu_driver_only_counts_as_gpu(now_ms):
    c = _gpu_cluster(
        node_type_id="Standard_DS3_v2",
        driver_node_type_id="Standard_NC6s_v3",
        autotermination_minutes=0,
    )
    assert len(classify_gpu_clusters([c], GPU_TYPES, now_ms, 8)) == 1


def test_terminated_gpu_cluster_not_flagged(now_ms):
    c = _gpu_cluster(state="TERMINATED", autotermination_minutes=0)
    assert classify_gpu_clusters([c], GPU_TYPES, now_ms, 8) == []


# --- find_vector_search_findings -----------------------------------------------

def _vs_endpoint(**overrides) -> dict:
    base = {
        "name": "vs-main",
        "creator": "someone@example.com",
        "status": "ONLINE",
        "created_ms": days_ago(10),
        "num_indexes": 3,
    }
    return {**base, **overrides}


def test_healthy_vector_search_endpoint_not_flagged(now_ms):
    assert find_vector_search_findings([_vs_endpoint()], now_ms, grace_hours=24) == []


def test_old_endpoint_with_zero_indexes_flagged(now_ms):
    findings = find_vector_search_findings(
        [_vs_endpoint(num_indexes=0)], now_ms, grace_hours=24
    )
    assert [f["action"] for f in findings] == ["delete-endpoint (manual)"]


def test_young_endpoint_with_zero_indexes_not_flagged(now_ms):
    e = _vs_endpoint(num_indexes=0, created_ms=hours_ago(2))
    assert find_vector_search_findings([e], now_ms, grace_hours=24) == []


def test_unhealthy_endpoint_flagged_for_review(now_ms):
    findings = find_vector_search_findings(
        [_vs_endpoint(status="RED_STATE")], now_ms, grace_hours=24
    )
    assert [f["action"] for f in findings] == ["review"]


# --- gpu_training policy file --------------------------------------------------

def test_gpu_training_policy_ships_with_required_tags():
    from dbx_platform.governance import load_local_policies

    policies = {p["name"]: p for p in load_local_policies("policies")}
    gpu = policies["gpu-training"]
    assert gpu["definition"]["custom_tags.team"]["isOptional"] is False
    assert gpu["definition"]["custom_tags.project"]["isOptional"] is False
    assert gpu["definition"]["node_type_id"]["type"] == "allowlist"

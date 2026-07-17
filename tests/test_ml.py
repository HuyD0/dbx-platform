from conftest import days_ago, hours_ago

from dbx_platform.ml import (
    classify_models,
    classify_serving_endpoints,
    find_stale_endpoints,
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

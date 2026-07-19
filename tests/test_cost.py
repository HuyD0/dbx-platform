from unittest.mock import MagicMock

from dbx_platform import cost
from dbx_platform.cost import (
    classify_cluster_utilization,
    classify_warehouse_utilization,
)
from dbx_platform.housekeeping import find_jobs_on_all_purpose
from dbx_platform.system_tables import load_query


def test_usage_report_is_scoped_to_current_workspace(monkeypatch):
    workspace = MagicMock()
    workspace.get_workspace_id.return_value = 12345
    captured = {}

    def read(_workspace, sql, warehouse_id, parameters):
        captured.update(sql=sql, warehouse_id=warehouse_id, parameters=parameters)
        return []

    monkeypatch.setattr(cost, "run_query", read)
    assert cost.usage_report(workspace, "warehouse-1", 30) == []
    assert "u.workspace_id = :workspace_id" in captured["sql"]
    assert captured["warehouse_id"] == "warehouse-1"
    assert captured["parameters"] == {"days": 30, "workspace_id": "12345"}


def test_product_spend_has_product_attribution_and_equal_comparison_windows(monkeypatch):
    workspace = MagicMock()
    workspace.get_workspace_id.return_value = 987
    captured = {}

    def read(_workspace, sql, _warehouse_id, parameters):
        captured.update(sql=sql, parameters=parameters)
        return []

    monkeypatch.setattr(cost, "run_query", read)
    assert cost.product_spend(workspace, "warehouse-1", 30) == []
    assert "u.billing_origin_product" in captured["sql"]
    assert "u.usage_metadata.app_name" in captured["sql"]
    assert "u.usage_metadata.database_instance_id" in captured["sql"]
    assert "u.usage_metadata.cluster_id" in captured["sql"]
    assert "u.usage_metadata.dlt_pipeline_id" in captured["sql"]
    assert captured["parameters"] == {
        "current_start_days": 29,
        "comparison_start_days": 59,
        "workspace_id": "987",
    }


def test_product_spend_query_reports_usage_units_instead_of_calling_everything_dbus():
    sql = load_query("product_spend")
    assert "u.usage_unit" in sql
    assert "u.usage_type" in sql
    assert "as dbus" not in sql.lower()


def test_all_cost_page_queries_are_scoped_to_the_current_workspace():
    for name in (
        "usage_last_30d",
        "product_spend",
        "job_run_cost",
        "cluster_utilization",
        "warehouse_utilization",
        "failed_run_cost",
    ):
        assert ":workspace_id" in load_query(name), name

# --- classify_cluster_utilization ----------------------------------------------
# Rows arrive from the Statement Execution API, so values are strings.

def _util_row(**overrides) -> dict:
    base = {
        "cluster_id": "c-1",
        "cluster_name": "etl",
        "creator": "someone@example.com",
        "avg_cpu_pct": "55.0",
        "p95_cpu_pct": "80.0",
        "avg_mem_pct": "70.0",
        "max_observed_workers": "4",
        "worker_count": "4",
        "min_autoscale_workers": None,
        "max_autoscale_workers": None,
        "worker_node_type": "Standard_DS3_v2",
        "list_cost_usd": "120.50",
    }
    return {**base, **overrides}


def test_busy_cluster_not_flagged():
    assert classify_cluster_utilization([_util_row()], 30, 50) == []


def test_idle_cluster_flagged_for_downsize():
    row = _util_row(p95_cpu_pct="12.0", avg_mem_pct="35.0")
    findings = classify_cluster_utilization([row], 30, 50)
    assert [f["action"] for f in findings] == ["downsize-node-or-workers"]


def test_low_cpu_but_high_memory_not_flagged():
    row = _util_row(p95_cpu_pct="12.0", avg_mem_pct="85.0")
    assert classify_cluster_utilization([row], 30, 50) == []


def test_cpu_at_threshold_not_flagged():
    row = _util_row(p95_cpu_pct="30.0", avg_mem_pct="20.0")
    assert classify_cluster_utilization([row], 30, 50) == []


def test_autoscale_max_never_reached_flagged():
    row = _util_row(min_autoscale_workers="2", max_autoscale_workers="8",
                    max_observed_workers="3")
    findings = classify_cluster_utilization([row], 30, 50)
    assert [f["action"] for f in findings] == ["lower-autoscale-max"]


def test_autoscale_max_reached_not_flagged():
    row = _util_row(min_autoscale_workers="2", max_autoscale_workers="8",
                    max_observed_workers="8")
    assert classify_cluster_utilization([row], 30, 50) == []


def test_findings_ranked_by_cost():
    cheap = _util_row(cluster_id="c-cheap", p95_cpu_pct="5.0", avg_mem_pct="5.0",
                      list_cost_usd="10.00")
    dear = _util_row(cluster_id="c-dear", p95_cpu_pct="5.0", avg_mem_pct="5.0",
                     list_cost_usd="900.00")
    findings = classify_cluster_utilization([cheap, dear], 30, 50)
    assert [f["cluster_id"] for f in findings] == ["c-dear", "c-cheap"]


def test_missing_metrics_not_flagged():
    row = _util_row(p95_cpu_pct=None, avg_mem_pct=None)
    assert classify_cluster_utilization([row], 30, 50) == []


# --- classify_warehouse_utilization --------------------------------------------

def _wh_row(**overrides) -> dict:
    base = {
        "warehouse_id": "wh-1",
        "list_cost_usd": "200.00",
        "query_count": "400",
        "avg_queue_seconds": "0.2",
    }
    return {**base, **overrides}


def test_busy_warehouse_not_flagged():
    assert classify_warehouse_utilization([_wh_row()], 50, 5) == []


def test_billed_idle_warehouse_flagged():
    row = _wh_row(query_count="0")
    findings = classify_warehouse_utilization([row], 50, 5)
    assert [f["action"] for f in findings] == ["reduce-auto-stop-or-delete"]


def test_low_query_volume_flagged():
    row = _wh_row(query_count="12")
    findings = classify_warehouse_utilization([row], 50, 5)
    assert [f["action"] for f in findings] == ["reduce-auto-stop-or-size"]


def test_queueing_warehouse_flagged_as_undersized():
    row = _wh_row(avg_queue_seconds="8.5")
    findings = classify_warehouse_utilization([row], 50, 5)
    assert [f["action"] for f in findings] == ["undersized-consider-scaling"]


def test_free_warehouse_with_no_queries_not_flagged():
    row = _wh_row(list_cost_usd="0", query_count="0")
    assert classify_warehouse_utilization([row], 50, 5) == []


# --- find_jobs_on_all_purpose ---------------------------------------------------

def _job(**overrides) -> dict:
    base = {
        "job_id": 1,
        "name": "nightly-etl",
        "creator": "someone@example.com",
        "tasks": [{"task_key": "main", "existing_cluster_id": "", "fixed_workers": 0}],
        "job_clusters": [],
    }
    return {**base, **overrides}


def test_job_cluster_job_not_flagged():
    assert find_jobs_on_all_purpose([_job()], fixed_workers_max=10) == []


def test_task_on_all_purpose_cluster_flagged():
    j = _job(tasks=[{"task_key": "main", "existing_cluster_id": "c-shared",
                     "fixed_workers": 0}])
    findings = find_jobs_on_all_purpose([j], fixed_workers_max=10)
    assert [f["action"] for f in findings] == ["move-to-job-cluster"]


def test_large_fixed_task_cluster_flagged():
    j = _job(tasks=[{"task_key": "main", "existing_cluster_id": "",
                     "fixed_workers": 24}])
    findings = find_jobs_on_all_purpose([j], fixed_workers_max=10)
    assert [f["action"] for f in findings] == ["enable-autoscale"]


def test_fixed_workers_at_threshold_not_flagged():
    j = _job(tasks=[{"task_key": "main", "existing_cluster_id": "",
                     "fixed_workers": 10}])
    assert find_jobs_on_all_purpose([j], fixed_workers_max=10) == []


def test_large_fixed_shared_job_cluster_flagged():
    j = _job(job_clusters=[{"key": "shared", "fixed_workers": 16}])
    findings = find_jobs_on_all_purpose([j], fixed_workers_max=10)
    assert [f["action"] for f in findings] == ["enable-autoscale"]

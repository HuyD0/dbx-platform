from conftest import days_ago, hours_ago

from dbx_platform.housekeeping import classify_clusters, find_orphaned_jobs


def _cluster(**overrides) -> dict:
    base = {
        "cluster_id": "c-1",
        "cluster_name": "test",
        "state": "TERMINATED",
        "source": "UI",
        "terminated_time": 0,
        "start_time": 0,
        "autotermination_minutes": 30,
        "pinned_by": "",
        "creator": "someone@example.com",
    }
    return {**base, **overrides}


def test_long_terminated_cluster_flagged_for_delete(now_ms):
    clusters = [_cluster(terminated_time=days_ago(40))]
    findings = classify_clusters(clusters, now_ms, stale_days=30, max_uptime_hours=24)
    assert len(findings) == 1
    assert findings[0]["action"] == "permanent-delete"


def test_exact_boundary_is_flagged(now_ms):
    clusters = [_cluster(terminated_time=days_ago(30))]
    findings = classify_clusters(clusters, now_ms, stale_days=30, max_uptime_hours=24)
    assert len(findings) == 1


def test_recently_terminated_cluster_not_flagged(now_ms):
    clusters = [_cluster(terminated_time=days_ago(29.5))]
    assert classify_clusters(clusters, now_ms, stale_days=30, max_uptime_hours=24) == []


def test_pinned_cluster_excluded(now_ms):
    clusters = [_cluster(terminated_time=days_ago(90), pinned_by="admin@example.com")]
    assert classify_clusters(clusters, now_ms, stale_days=30, max_uptime_hours=24) == []


def test_job_cluster_excluded(now_ms):
    clusters = [_cluster(terminated_time=days_ago(90), source="JOB")]
    assert classify_clusters(clusters, now_ms, stale_days=30, max_uptime_hours=24) == []


def test_running_cluster_with_autotermination_not_flagged(now_ms):
    clusters = [_cluster(state="RUNNING", start_time=hours_ago(3),
                         autotermination_minutes=30)]
    assert classify_clusters(clusters, now_ms, stale_days=30, max_uptime_hours=24) == []


def test_running_cluster_without_autotermination_flagged(now_ms):
    clusters = [_cluster(state="RUNNING", start_time=hours_ago(1),
                         autotermination_minutes=0)]
    findings = classify_clusters(clusters, now_ms, stale_days=30, max_uptime_hours=24)
    assert len(findings) == 1
    assert findings[0]["action"] == "terminate"
    assert "autotermination disabled" in findings[0]["reason"]


def test_long_running_cluster_flagged(now_ms):
    clusters = [_cluster(state="RUNNING", start_time=hours_ago(30),
                         autotermination_minutes=120)]
    findings = classify_clusters(clusters, now_ms, stale_days=30, max_uptime_hours=24)
    assert len(findings) == 1
    assert "running 30h" in findings[0]["reason"]


def test_orphaned_job_detection():
    jobs = [
        {"job_id": 1, "name": "ok", "creator": "alive@example.com", "has_schedule": True},
        {"job_id": 2, "name": "orphan", "creator": "gone@example.com", "has_schedule": True},
        {"job_id": 3, "name": "no-owner", "creator": "", "has_schedule": False},
    ]
    orphans = find_orphaned_jobs(jobs, {"alive@example.com"})
    assert {o["job_id"] for o in orphans} == {2, 3}


def test_orphan_check_is_case_insensitive():
    jobs = [{"job_id": 1, "name": "j", "creator": "Alice@Example.com", "has_schedule": False}]
    assert find_orphaned_jobs(jobs, {"alice@example.com"}) == []

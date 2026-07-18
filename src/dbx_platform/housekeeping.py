"""Housekeeping: stale clusters and orphaned jobs.

Each check is split into *fetch* (thin SDK wrapper returning plain dicts) and
*decide* (pure function — unit-testable offline). Apply paths are invoked only
by the CLI after the dry-run/confirmation guard.
"""

from __future__ import annotations

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.jobs import PauseStatus

MS_PER_DAY = 86_400_000
MS_PER_HOUR = 3_600_000


# --- stale clusters -------------------------------------------------------

def fetch_clusters(w: WorkspaceClient) -> list[dict]:
    out = []
    for c in w.clusters.list():
        out.append(
            {
                "cluster_id": c.cluster_id,
                "cluster_name": c.cluster_name,
                "state": c.state.value if c.state else "",
                "source": c.cluster_source.value if c.cluster_source else "",
                "terminated_time": c.terminated_time or 0,
                "start_time": c.start_time or 0,
                "autotermination_minutes": c.autotermination_minutes or 0,
                "pinned_by": getattr(c, "pinned_by_user_name", None) or "",
                "creator": c.creator_user_name or "",
            }
        )
    return out


def classify_clusters(
    clusters: list[dict], now_ms: int, stale_days: int, max_uptime_hours: int
) -> list[dict]:
    """Pure decision logic. Returns findings with a proposed action.

    - Terminated (non-pinned, non-job) clusters idle >= stale_days:
      candidates for an owner-reviewed retention decision. Resource deletion
      is unsupported in Mission Control v1.
    - Running clusters up >= max_uptime_hours, or with autotermination
      disabled: candidates for terminate / review.
    """
    findings = []
    for c in clusters:
        if c.get("source") == "JOB":
            continue  # ephemeral job clusters are managed by the jobs service
        if c.get("state") == "TERMINATED":
            if c.get("pinned_by"):
                continue
            if not c.get("terminated_time"):
                continue
            idle_days = (now_ms - c["terminated_time"]) / MS_PER_DAY
            if idle_days >= stale_days:
                findings.append(
                    {
                        "cluster_id": c["cluster_id"],
                        "cluster_name": c["cluster_name"],
                        "creator": c["creator"],
                        "reason": f"terminated {idle_days:.0f}d ago (threshold {stale_days}d)",
                        "action": "review-retention",
                    }
                )
        elif c.get("state") == "RUNNING":
            reasons = []
            if c.get("autotermination_minutes", 0) == 0:
                reasons.append("autotermination disabled")
            if c.get("start_time"):
                uptime_h = (now_ms - c["start_time"]) / MS_PER_HOUR
                if uptime_h >= max_uptime_hours:
                    reasons.append(f"running {uptime_h:.0f}h (threshold {max_uptime_hours}h)")
            if reasons:
                findings.append(
                    {
                        "cluster_id": c["cluster_id"],
                        "cluster_name": c["cluster_name"],
                        "creator": c["creator"],
                        "reason": "; ".join(reasons),
                        "action": "terminate",
                    }
                )
    return findings


def apply_cluster_findings(w: WorkspaceClient, findings: list[dict]) -> list[str]:
    """Terminate recoverable clusters only.

    Permanent resource deletion is deliberately unsupported by Mission Control
    v1. Old terminated clusters remain findings for owner review.
    """
    done = []
    for f in findings:
        if f["action"] == "terminate":
            w.clusters.delete(cluster_id=f["cluster_id"])  # delete == terminate, recoverable
            done.append(f"terminated {f['cluster_id']} ({f['cluster_name']})")
        elif f["action"] == "review-retention":
            done.append(
                f"left terminated cluster {f['cluster_id']} unchanged "
                "(resource deletion is unsupported)"
            )
    return done


# --- orphaned jobs --------------------------------------------------------

def fetch_jobs(w: WorkspaceClient) -> list[dict]:
    out = []
    for j in w.jobs.list():
        settings = j.settings
        pause_states = {
            name: (
                getattr(getattr(settings, name), "pause_status", None).value
                if getattr(getattr(settings, name), "pause_status", None)
                else ""
            )
            for name in ("schedule", "trigger", "continuous")
            if settings is not None and getattr(settings, name, None) is not None
        }
        out.append(
            {
                "job_id": j.job_id,
                "name": settings.name if settings else "",
                "creator": j.creator_user_name or "",
                "has_schedule": bool(settings and (settings.schedule or settings.trigger
                                                   or settings.continuous)),
                "pause_states": pause_states,
            }
        )
    return out


def fetch_active_principals(w: WorkspaceClient) -> set[str]:
    principals: set[str] = set()
    for u in w.users.list(attributes="userName,active"):
        if u.active is not False and u.user_name:
            principals.add(u.user_name.lower())
    for sp in w.service_principals.list():
        if sp.active is not False:
            if sp.application_id:
                principals.add(sp.application_id.lower())
            if sp.display_name:
                principals.add(sp.display_name.lower())
    return principals


def find_orphaned_jobs(jobs: list[dict], active_principals: set[str]) -> list[dict]:
    """Pure decision logic: jobs whose creator no longer exists / is inactive."""
    orphans = []
    for j in jobs:
        owner = j.get("creator", "").lower()
        if not owner:
            orphans.append({**j, "reason": "no creator recorded (created via API?)"})
        elif owner not in active_principals:
            orphans.append({**j, "reason": f"creator '{j['creator']}' not an active principal"})
    return orphans


# --- jobs on all-purpose compute -----------------------------------------

def fetch_jobs_with_clusters(w: WorkspaceClient) -> list[dict]:
    out = []
    for j in w.jobs.list(expand_tasks=True):
        s = j.settings
        tasks = []
        for t in (s.tasks if s else None) or []:
            nc = t.new_cluster
            tasks.append(
                {
                    "task_key": t.task_key or "",
                    "existing_cluster_id": t.existing_cluster_id or "",
                    "fixed_workers": (nc.num_workers or 0) if nc and not nc.autoscale else 0,
                }
            )
        job_clusters = []
        for jc in (s.job_clusters if s else None) or []:
            nc = jc.new_cluster
            job_clusters.append(
                {
                    "key": jc.job_cluster_key or "",
                    "fixed_workers": (nc.num_workers or 0) if nc and not nc.autoscale else 0,
                }
            )
        out.append(
            {
                "job_id": j.job_id,
                "name": s.name if s else "",
                "creator": j.creator_user_name or "",
                "tasks": tasks,
                "job_clusters": job_clusters,
            }
        )
    return out


def find_jobs_on_all_purpose(jobs: list[dict], fixed_workers_max: int) -> list[dict]:
    """Pure decision logic: jobs paying the all-purpose premium or pinning
    large fixed-size clusters.

    - Tasks bound to an all-purpose cluster (existing_cluster_id) pay roughly
      double the job-compute DBU rate.
    - Task/job clusters with autoscale disabled and more than
      ``fixed_workers_max`` workers hold capacity whether used or not.
    """
    findings = []

    def flag(j: dict, reason: str, action: str) -> None:
        findings.append(
            {"job_id": j["job_id"], "name": j["name"], "creator": j["creator"],
             "reason": reason, "action": action}
        )

    for j in jobs:
        for t in j.get("tasks", []):
            if t.get("existing_cluster_id"):
                flag(j, f"task '{t['task_key']}' runs on all-purpose cluster "
                        f"{t['existing_cluster_id']} (~2x job-compute DBU rate)",
                     "move-to-job-cluster")
            elif t.get("fixed_workers", 0) > fixed_workers_max:
                flag(j, f"task '{t['task_key']}' pins {t['fixed_workers']} fixed workers "
                        f"without autoscale (threshold {fixed_workers_max})",
                     "enable-autoscale")
        for jc in j.get("job_clusters", []):
            if jc.get("fixed_workers", 0) > fixed_workers_max:
                flag(j, f"job cluster '{jc['key']}' pins {jc['fixed_workers']} fixed "
                        f"workers without autoscale (threshold {fixed_workers_max})",
                     "enable-autoscale")
    return findings


def pause_job(w: WorkspaceClient, job_id: int) -> bool:
    """Pause a job's schedule/trigger/continuous run. Never deletes. Returns
    True if anything was changed."""
    from databricks.sdk.service.jobs import JobSettings

    job = w.jobs.get(job_id)
    s = job.settings
    if s is None:
        return False
    changed = False
    new = JobSettings()
    for attr in ("schedule", "trigger", "continuous"):
        block = getattr(s, attr, None)
        if block is not None and block.pause_status != PauseStatus.PAUSED:
            block.pause_status = PauseStatus.PAUSED
            setattr(new, attr, block)
            changed = True
    if changed:
        w.jobs.update(job_id=job_id, new_settings=new)
    return changed

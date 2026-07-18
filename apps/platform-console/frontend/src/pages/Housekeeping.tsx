import { PlanActionButton } from "../components/ActionPlanDialog";
import { FindingsSection } from "../components/FindingsSection";

export function Housekeeping() {
  return (
    <div className="space-y-4">
      <FindingsSection
        title="Stale clusters"
        subtitle="Terminated and forgotten, or running past the uptime threshold"
        path="/api/housekeeping/stale-clusters"
        emptyMessage="No stale clusters."
        actionSlot={<PlanActionButton action="stale-clusters" label="Plan cleanup" />}
      />
      <FindingsSection
        title="Orphaned jobs"
        subtitle="Creator no longer exists or is inactive — remediation pauses, never deletes"
        path="/api/housekeeping/orphaned-jobs"
        emptyMessage="No orphaned jobs."
        actionSlot={<PlanActionButton action="orphaned-jobs" label="Plan pause" />}
      />
      <FindingsSection
        title="Jobs on all-purpose compute"
        subtitle="Paying the all-purpose premium or pinning large fixed clusters"
        path="/api/housekeeping/jobs-on-all-purpose"
        emptyMessage="No jobs on all-purpose compute."
      />
    </div>
  );
}

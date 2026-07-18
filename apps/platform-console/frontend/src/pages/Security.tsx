import { PlanActionButton } from "../components/ActionPlanDialog";
import { FindingsSection } from "../components/FindingsSection";

export function Security() {
  return (
    <div className="space-y-4">
      <FindingsSection
        title="PAT token audit"
        subtitle="Never-expires, over the age threshold, or expiring soon — remediation revokes only over-age tokens"
        path="/api/security/token-audit"
        emptyMessage="No token findings."
        actionSlot={<PlanActionButton action="token-revoke" label="Plan revoke" />}
      />
      <FindingsSection
        title="Inactive users"
        subtitle="Active SCIM users with no audited activity — report-only; deactivation stays with your IdP"
        path="/api/security/inactive-users"
        emptyMessage="No inactive users."
      />
    </div>
  );
}

import { PageHeader } from "../components/ui";
import { Governance } from "./Governance";

/** Data governance promoted to a top-level surface: policy-as-code drift,
 * tag enforcement and the spend that escapes attribution. */
export function DataGovernance() {
  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Stewardship"
        title="Data Governance"
        description="Keep cluster policies, attribution tags and ownership honest — the tags enforced here are what make every cost view attributable."
      />
      <Governance />
    </div>
  );
}

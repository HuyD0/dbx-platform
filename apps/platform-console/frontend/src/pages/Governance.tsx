import { useQuery } from "@tanstack/react-query";
import { PlanActionButton } from "../components/ActionPlanDialog";
import { FindingsSection } from "../components/FindingsSection";
import { apiGet } from "../lib/api";
import type { Envelope } from "../lib/types";
import { Badge, Card, ErrorState, SectionTitle, Skeleton } from "../components/ui";

type Drift = Record<"create" | "update" | "unchanged" | "unmanaged", { name: string }[]>;

const DRIFT_TONE = {
  create: "warning",
  update: "warning",
  unchanged: "good",
  unmanaged: "info",
} as const;

function PolicyDrift() {
  const query = useQuery({
    queryKey: ["/api/governance/policy-drift"],
    queryFn: () => apiGet<Envelope<Drift>>("/api/governance/policy-drift"),
    staleTime: 60_000,
    retry: false,
  });
  return (
    <Card>
      <SectionTitle
        title="Cluster policy drift"
        subtitle="Git (policies/*.json) vs workspace — sync never deletes unmanaged policies"
        right={<PlanActionButton action="policy-sync" label="Plan sync" />}
      />
      {query.isPending ? (
        <Skeleton rows={2} />
      ) : query.isError ? (
        <ErrorState error={query.error} />
      ) : (
        <div className="space-y-2">
          {(Object.keys(DRIFT_TONE) as (keyof Drift)[]).map((bucket) => {
            const names = query.data.data[bucket] ?? [];
            return (
              <div key={bucket} className="flex flex-wrap items-center gap-2 text-xs">
                <Badge tone={DRIFT_TONE[bucket]}>
                  {bucket}: {names.length}
                </Badge>
                <span className="text-ink-2">{names.map((n) => n.name).join(", ") || "—"}</span>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

export function Governance() {
  return (
    <div className="space-y-4">
      <PolicyDrift />
      <FindingsSection
        title="Tag compliance"
        subtitle="Resources missing required tags"
        path="/api/governance/tag-compliance"
        emptyMessage="All resources carry the required tags."
      />
      <FindingsSection
        title="Tag recommendations"
        subtitle="Suggested fixes: mistyped keys, values inferred from names, creators for owner keys"
        path="/api/governance/tag-recommendations"
        emptyMessage="No recommendations."
      />
      <FindingsSection
        title="Untagged spend"
        subtitle="Share of list cost carrying no custom tags"
        path="/api/governance/untagged-spend"
        emptyMessage="No untagged spend."
      />
    </div>
  );
}

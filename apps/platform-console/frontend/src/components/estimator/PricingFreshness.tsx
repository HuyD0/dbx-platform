import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { Envelope, PricingStatus } from "../../lib/types";
import { Badge, statusTone } from "../ui";

/** The reproducibility stamp: which price snapshot the numbers came from and
 * whether any rate keys failed to match a live meter. */
export function PricingFreshness({ snapshotDate }: { snapshotDate: string }) {
  const status = useQuery({
    queryKey: ["estimator", "pricing-status"],
    queryFn: () => apiGet<Envelope<PricingStatus>>("/api/estimator/pricing-status"),
    staleTime: 5 * 60_000,
  });
  const health = status.data?.data.health;
  const findings = status.data?.data.coverage_findings ?? [];
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
      <Badge tone="info">prices from {snapshotDate || "unknown snapshot"}</Badge>
      {health && <Badge tone={statusTone(health.status)}>pricing {health.status}</Badge>}
      {findings.length > 0 && (
        <Badge tone="warning">{findings.length} price meter(s) need attention</Badge>
      )}
      <span>
        Estimates are order-of-magnitude budgeting numbers at public list prices —
        negotiated discounts land lower.
      </span>
    </div>
  );
}

import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../../lib/api";
import type { SavedEstimateSummary, SimilarEstimatesResponse } from "../../lib/types";
import { timeAgo } from "../../lib/format";
import { Badge, Card, SectionTitle } from "../ui";

/** "Pull up similar questions": deterministic structured matching against the
 * saved-estimate library — an exact requirements match surfaces the existing
 * estimate; same-pattern, same-order-of-magnitude matches are offered for
 * comparison and duplicate-&-edit. Never semantic, never fuzzy. */
export function SimilarEstimates({
  pattern,
  monthlyRequests,
  requirementsHash,
  onReuse,
}: {
  pattern: string;
  monthlyRequests: number;
  requirementsHash?: string;
  onReuse: (estimate: SavedEstimateSummary) => void;
}) {
  const query = useQuery({
    queryKey: ["estimator", "similar", pattern, monthlyRequests, requirementsHash],
    queryFn: () =>
      apiGet<SimilarEstimatesResponse>("/api/estimator/estimates/similar", {
        pattern,
        monthly_requests: monthlyRequests,
        requirements_hash: requirementsHash ?? "",
      }),
    enabled: Boolean(pattern && monthlyRequests > 0),
    staleTime: 60_000,
  });
  const data = query.data;
  if (!data || (!data.exact_match && data.similar.length === 0)) return null;

  const card = (estimate: SavedEstimateSummary, exact: boolean) => (
    <li
      key={estimate.estimate_id}
      className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-hairline p-3"
    >
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-ink">{estimate.title}</p>
        <p className="text-xs text-muted">
          {estimate.monthly_requests.toLocaleString("en-US")} requests / month ·
          reviews {estimate.rigor_pct}% · saved {timeAgo(estimate.created_at)}
          {estimate.snapshot_date && ` · prices ${estimate.snapshot_date}`}
        </p>
      </div>
      <div className="flex items-center gap-2">
        {exact && <Badge tone="good">same inputs</Badge>}
        <button
          type="button"
          onClick={() => onReuse(estimate)}
          className="rounded-lg border border-hairline px-3 py-1.5 text-xs text-ink-2 hover:text-ink"
        >
          Use as starting point
        </button>
      </div>
    </li>
  );

  return (
    <Card>
      <SectionTitle
        title={
          data.exact_match
            ? "This estimate already exists"
            : `${data.similar.length} similar past estimate${data.similar.length === 1 ? "" : "s"}`
        }
        subtitle="Someone already priced a solution with this shape — compare before re-deriving the numbers."
      />
      <ul className="space-y-2" aria-label="Similar past estimates">
        {data.exact_match && card(data.exact_match, true)}
        {data.similar.map((estimate) => card(estimate, false))}
      </ul>
    </Card>
  );
}

import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";
import { Card, PageHeader, SectionTitle, Skeleton } from "../components/ui";
import { apiGet } from "../lib/api";
import type { Envelope, Row } from "../lib/types";
import { Governance } from "./Governance";

function useStage<T>(path: string, params?: Record<string, string | number>) {
  return useQuery({
    queryKey: [path, params],
    queryFn: () => apiGet<Envelope<T>>(path, params),
    staleTime: 60_000,
    retry: false,
  });
}

function Stage({
  step,
  title,
  stat,
  detail,
  to,
  cta,
  pending,
}: {
  step: number;
  title: string;
  stat: string;
  detail: string;
  to: string;
  cta: string;
  pending: boolean;
}) {
  return (
    <Card className="flex h-full flex-col">
      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted">
        Stage {step}
      </div>
      <h3 className="mt-1 text-sm font-semibold text-ink">{title}</h3>
      {pending ? (
        <Skeleton rows={1} />
      ) : (
        <div className="mt-2 text-2xl font-semibold tabular-nums text-ink">{stat}</div>
      )}
      <p className="mt-1 flex-1 text-xs leading-5 text-muted">{detail}</p>
      <Link
        to={to}
        className="mt-3 inline-flex w-fit items-center gap-1 rounded-lg border border-grid px-3 py-1.5 text-xs font-medium text-ink hover:bg-hairline"
      >
        {cta} <ArrowRight className="h-3 w-3" />
      </Link>
    </Card>
  );
}

/** The payoff view for tag enforcement: policies force team/project tags →
 * how much spend is tagged → who the money belongs to. Each stage links to
 * the place that fixes it. */
function TagFunnel() {
  const compliance = useStage<Row[]>("/api/governance/tag-compliance");
  const untagged = useStage<Row[]>("/api/governance/untagged-spend", { days: 30 });
  const attribution = useStage<Row[]>("/api/cost/attribution", { dimension: "team", days: 30 });

  const missing = compliance.data?.data.length;
  const untaggedPct = untagged.data?.data?.[0]?.untagged_pct;
  const attributionRows = attribution.data?.data ?? [];
  const totalCost = attributionRows.reduce((sum, row) => sum + Number(row.list_cost ?? 0), 0);
  const unallocated = attributionRows
    .filter((row) => String(row.x_team ?? "") === "unallocated")
    .reduce((sum, row) => sum + Number(row.list_cost ?? 0), 0);
  const attributedPct =
    totalCost > 0 ? Math.round(((totalCost - unallocated) / totalCost) * 100) : null;

  return (
    <Card>
      <SectionTitle
        title="Tag coverage funnel"
        subtitle="Enforcement only pays off when spend ends up attributed — follow the funnel left to right"
      />
      <div className="grid gap-3 md:grid-cols-3">
        <Stage
          step={1}
          title="Enforced by policy"
          stat={
            compliance.isError ? "—" : missing === 0 ? "All tagged" : `${missing ?? "—"} missing`
          }
          detail="Live clusters and jobs missing the required team/project tags the cluster policies mandate."
          to="/data-governance"
          cta="Review compliance below"
          pending={compliance.isPending}
        />
        <Stage
          step={2}
          title="Tagged spend"
          stat={
            untagged.isError || untaggedPct === undefined
              ? "—"
              : `${(100 - Number(untaggedPct)).toFixed(1)}% tagged`
          }
          detail="Share of the last 30 days of list cost carrying at least one custom tag."
          to="/data-governance"
          cta="See tag recommendations"
          pending={untagged.isPending}
        />
        <Stage
          step={3}
          title="Attributed to a team"
          stat={attribution.isError || attributedPct === null ? "—" : `${attributedPct}%`}
          detail="Share of the last 30 days of list cost that lands on a named team instead of 'unallocated'."
          to="/cost?tab=attribution"
          cta="Open spend by team"
          pending={attribution.isPending}
        />
      </div>
    </Card>
  );
}

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
      <TagFunnel />
      <Governance />
    </div>
  );
}

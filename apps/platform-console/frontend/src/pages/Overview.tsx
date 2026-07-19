import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { apiGet } from "../lib/api";
import { timeAgo } from "../lib/format";
import type { DashboardInfo, Envelope, OverviewData } from "../lib/types";
import { BarList } from "../components/BarList";
import { aggregateProductSpend } from "../components/ProductSpendBreakdown";
import { AsOf, Card, ErrorState, SectionTitle, Skeleton, StatTile } from "../components/ui";

export function Overview() {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["overview"],
    queryFn: () => apiGet<Envelope<OverviewData>>("/api/overview"),
    staleTime: 60_000,
    retry: false,
  });
  const dashboards = useQuery({
    queryKey: ["dashboards"],
    queryFn: () => apiGet<Envelope<DashboardInfo[]>>("/api/dashboards"),
    staleTime: 300_000,
    retry: false,
  });

  const refresh = () =>
    queryClient.fetchQuery({
      queryKey: ["overview"],
      queryFn: () => apiGet<Envelope<OverviewData>>("/api/overview", { refresh: true }),
    });

  if (query.isPending) {
    return (
      <div className="space-y-4">
        <Skeleton rows={6} />
      </div>
    );
  }
  if (query.isError) return <ErrorState error={query.error} />;

  const d = query.data.data;
  const findings = d.findings.data;
  const spendRows = d.spend.data ?? [];
  const productSpend = aggregateProductSpend(spendRows);
  const spendTotal = productSpend.reduce((acc, product) => acc + product.current, 0);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted">
          Findings from stored check runs; run any check fresh from its area page.
        </p>
        <AsOf
          asOf={query.data.as_of}
          cached={query.data.cached}
          onRefresh={refresh}
          refreshing={query.isFetching}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label="Open findings"
          value={findings ? findings.total : "—"}
          tone={findings && findings.total > 0 ? "warning" : "good"}
          hint={findings?.run_ts ? `last run ${timeAgo(findings.run_ts)}` : "no stored run yet"}
        />
        <StatTile
          label="Areas affected"
          value={findings ? Object.keys(findings.by_area).length : "—"}
        />
        <StatTile
          label={`Workspace spend (${spendRows.length ? "30d" : "n/a"})`}
          value={
            spendTotal
              ? spendTotal.toLocaleString("en-US", {
                  style: "currency",
                  currency: "USD",
                  maximumFractionDigits: 0,
                })
              : "—"
          }
        />
        <StatTile
          label="Latest AI digest"
          value={d.digest.data?.latest_run_ts ? timeAgo(d.digest.data.latest_run_ts) : "none"}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <SectionTitle
            title="Findings by area"
            subtitle="Latest stored run of the platform checks"
          />
          {findings ? (
            Object.keys(findings.by_area).length > 0 ? (
              <BarList
                money={false}
                data={Object.entries(findings.by_area).map(([label, value]) => ({
                  label,
                  value,
                }))}
              />
            ) : (
              <p className="text-sm text-muted">No stored findings — the platform is clean.</p>
            )
          ) : (
            <p className="text-xs text-muted">{d.findings.error?.message}</p>
          )}
        </Card>
        <Card>
          <SectionTitle title="Spend by product" subtitle="Workspace list cost, last 30 days" />
          {d.spend.data ? (
            <BarList
              data={productSpend.map((product) => ({
                label: product.label,
                value: product.current,
              }))}
            />
          ) : (
            <p className="text-xs text-muted">{d.spend.error?.message}</p>
          )}
        </Card>
      </div>

      {dashboards.data && dashboards.data.data.length > 0 && (
        <Card>
          <SectionTitle
            title="Lakeview dashboards"
            subtitle="Deep-dive dashboards deployed by this bundle"
          />
          <div className="flex flex-wrap gap-2">
            {dashboards.data.data.map((dash) => (
              <a
                key={dash.url}
                href={dash.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 rounded-lg border border-grid px-3 py-1.5 text-xs text-ink hover:bg-hairline"
              >
                {dash.name}
                <ExternalLink className="h-3 w-3 text-muted" />
              </a>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

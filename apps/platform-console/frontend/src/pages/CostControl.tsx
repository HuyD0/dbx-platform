import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  BadgeDollarSign,
  CircleAlert,
  Layers3,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { CostTrendChart, costCategoryColor } from "../components/CostTrendChart";
import {
  AsOf,
  Badge,
  Card,
  DataHealthList,
  EmptyState,
  ErrorState,
  PageHeader,
  SectionTitle,
  Skeleton,
  statusTone,
} from "../components/ui";
import { apiGet } from "../lib/api";
import { currency, percent } from "../lib/format";
import type { CostOverview, Envelope } from "../lib/types";

const WINDOWS = [7, 30, 90] as const;

function deltaLabel(value: number | null) {
  if (value == null) return "No comparable prior period";
  if (value === 0) return "Flat versus prior period";
  return `${value > 0 ? "+" : ""}${percent(value)} versus prior period`;
}

function Delta({ value }: { value: number | null }) {
  const rising = (value ?? 0) > 0;
  const Icon = rising ? TrendingUp : TrendingDown;
  return (
    <span
      className={`inline-flex items-center gap-1 text-xs ${
        rising ? "text-status-serious" : "text-status-good"
      }`}
    >
      <Icon className="h-3.5 w-3.5" />
      {deltaLabel(value)}
    </span>
  );
}

export function CostControl() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const requestedDays = Number(params.get("window")?.replace("d", "") ?? 30);
  const days = WINDOWS.includes(requestedDays as (typeof WINDOWS)[number])
    ? requestedDays
    : 30;
  const query = useQuery({
    queryKey: ["/api/cost/overview", days],
    queryFn: () => apiGet<Envelope<CostOverview>>(`/api/cost/overview?window=${days}d`),
    staleTime: 60_000,
    retry: false,
  });

  const header = (
    <PageHeader
      eyebrow="Platform FinOps"
      title="Cost Control"
      description="Azure and Databricks cost in one scoped view—what changed, what drove it, and where spend may be running away."
      actions={
        <div
          className="inline-flex rounded-xl border border-grid bg-hairline/20 p-1"
          aria-label="Cost window"
        >
          {WINDOWS.map((window) => (
            <button
              key={window}
              type="button"
              aria-pressed={days === window}
              onClick={() => {
                const next = new URLSearchParams(params);
                if (window === 30) next.delete("window");
                else next.set("window", `${window}d`);
                setParams(next, { replace: true });
              }}
              className={`rounded-lg px-2.5 py-1.5 text-xs font-medium ${
                days === window ? "bg-surface text-ink shadow-sm" : "text-muted hover:text-ink"
              }`}
            >
              {window}d
            </button>
          ))}
        </div>
      }
    />
  );

  if (query.isPending) {
    return (
      <div className="space-y-5">
        {header}
        <Skeleton rows={12} />
      </div>
    );
  }
  if (query.isError) {
    return (
      <div className="space-y-5">
        {header}
        <ErrorState error={query.error} />
      </div>
    );
  }

  const { data, as_of: asOf, cached } = query.data;
  const total = data.totals[0];
  const databricks = data.components.find(
    (component) => component.component === "Azure Databricks",
  );
  const infrastructure = data.components.find(
    (component) => component.component === "Other Azure infrastructure",
  );
  const selectedCurrency = total?.currency;
  const chartPoints = selectedCurrency
    ? data.series.filter((point) => point.currency === selectedCurrency)
    : [];
  const anomalies = data.anomalies.slice(0, 4);
  const alignment = data.billing_alignment ?? {
    status: "unavailable",
    variance_count: 0,
    unmatched_count: 0,
    latest_azure_date: null,
    latest_databricks_date: null,
    azure_lag_days: null,
    databricks_lag_days: null,
    azure_totals: [],
    databricks_totals: [],
    largest_pattern_variance: null,
    money_comparable: false,
    notes: "Daily alignment is unavailable.",
  };
  const alignmentLabel = {
    aligned: "Aligned",
    variances_found: "Variances found",
    delayed_source: "Source delayed",
    unavailable: "Unavailable",
  }[alignment.status];

  return (
    <div className="space-y-5">
      {header}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          <Badge tone="info">{data.scope.environment}</Badge>
          <span>{data.scope.resource_groups.length} scoped resource groups</span>
          <span>Azure ActualCost</span>
        </div>
        <AsOf
          asOf={asOf}
          cached={cached}
          onRefresh={() => query.refetch()}
          refreshing={query.isFetching}
        />
      </div>

      <div className="grid min-w-0 grid-cols-2 gap-2.5 sm:gap-3 xl:grid-cols-4">
        <Link to="/cost?tab=categories" className="min-w-0 rounded-2xl">
          <Card className="glass-hover-accent h-full min-w-0 p-3 sm:p-4">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-muted">Total platform cost</span>
              <BadgeDollarSign className="h-4 w-4 text-accent" />
            </div>
            <div className="mt-2 break-words text-lg font-semibold tabular-nums text-ink sm:text-3xl">
              {total ? currency(total.cost, total.currency) : "—"}
            </div>
            <div className="mt-2 hidden sm:block">
              <Delta value={total?.period_delta_pct ?? null} />
            </div>
            <p className="mt-2 hidden text-[11px] text-muted sm:block">
              Authoritative Azure billed actuals · click to explore
            </p>
          </Card>
        </Link>

        <Link to="/cost?tab=databricks" className="min-w-0 rounded-2xl">
          <Card className="glass-hover-accent h-full min-w-0 p-3 sm:p-4">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-muted">Azure Databricks</span>
              <Layers3 className="h-4 w-4 text-series-1" />
            </div>
            <div className="mt-2 break-words text-lg font-semibold tabular-nums text-ink sm:text-3xl">
              {databricks ? currency(databricks.cost, databricks.currency) : "—"}
            </div>
            <p className="mt-1 text-xs text-muted">
              {databricks ? `${databricks.share_pct}% of platform total` : "No billed cost"}
            </p>
            <p className="mt-3 hidden text-[11px] text-muted sm:block">
              Open workload and SKU attribution
            </p>
          </Card>
        </Link>

        <Link to="/cost?tab=categories&component=azure" className="min-w-0 rounded-2xl">
          <Card className="glass-hover-accent h-full min-w-0 p-3 sm:p-4">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-muted">Other Azure</span>
              <span className="h-2.5 w-2.5 rounded-full bg-series-3" />
            </div>
            <div className="mt-2 break-words text-lg font-semibold tabular-nums text-ink sm:text-3xl">
              {infrastructure ? currency(infrastructure.cost, infrastructure.currency) : "—"}
            </div>
            <p className="mt-1 text-xs text-muted">
              {infrastructure
                ? `${infrastructure.share_pct}% across compute, storage and network`
                : "No billed infrastructure cost"}
            </p>
            <p className="mt-3 hidden text-[11px] text-muted sm:block">
              Open service categories
            </p>
          </Card>
        </Link>

        <Link to="/cost?tab=alignment" className="min-w-0 rounded-2xl">
          <Card className="glass-hover-accent h-full min-w-0 p-3 sm:p-4">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-muted">Variance watch</span>
              <CircleAlert
                className={`h-4 w-4 ${
                  alignment.status === "aligned" ? "text-status-good" : "text-status-warning"
                }`}
              />
            </div>
            <div className="mt-2 text-lg font-semibold tabular-nums text-ink sm:text-3xl">
              {alignment.variance_count}
            </div>
            <p className="mt-1 text-xs text-muted">
              {alignmentLabel}
              {alignment.unmatched_count > 0
                ? ` · ${alignment.unmatched_count} unmatched`
                : ""}
            </p>
            <p className="mt-3 hidden text-[11px] text-muted sm:block">
              Azure through {alignment.latest_azure_date ?? "—"} · Databricks through{" "}
              {alignment.latest_databricks_date ?? "—"}
            </p>
          </Card>
        </Link>
      </div>

      <Card>
        <SectionTitle
          title="Platform cost trend"
          subtitle="Daily Azure actuals stacked by service category; select a day to inspect it"
          right={
            <Link
              to="/cost?tab=categories"
              className="inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline"
            >
              Open Cost Explorer <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          }
        />
        <CostTrendChart
          points={chartPoints}
          onSelectDate={(date) => navigate(`/cost?tab=categories&date=${date}`)}
        />
        <div className="mt-3 flex flex-wrap gap-3 border-t border-grid pt-3 text-[11px] text-muted">
          {data.categories
            .filter((category) => !selectedCurrency || category.currency === selectedCurrency)
            .map((category) => (
              <span key={category.category} className="inline-flex items-center gap-1.5">
                <span
                  className="h-2 w-2 rounded-full"
                  data-testid="cost-legend-dot"
                  data-category={category.category}
                  style={{
                    backgroundColor: costCategoryColor(
                      String(category.category ?? "Other"),
                    ),
                  }}
                  aria-hidden="true"
                />
                {category.category} {category.share_pct}%
              </span>
            ))}
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[1.35fr_1fr]">
        <Card>
          <SectionTitle
            title="Runaway spend signals"
            subtitle="Material changes worth investigating—not automatic remediation"
            right={
              <Badge tone={data.anomalies.length ? "warning" : "good"}>
                {data.anomalies.length}
              </Badge>
            }
          />
          {anomalies.length === 0 ? (
            <EmptyState message="No daily spike or 7-day acceleration crossed the configured materiality thresholds." />
          ) : (
            <div className="space-y-2">
              {anomalies.map((anomaly, index) => (
                <Link
                  key={anomaly.id}
                  to={`/cost/anomalies/${encodeURIComponent(anomaly.id)}?days=${days}`}
                  className={`group min-w-0 flex-col items-start gap-2 rounded-xl border border-grid bg-page/25 p-3 hover:border-accent/40 hover:bg-hairline/30 sm:flex-row sm:items-center sm:justify-between sm:gap-3 ${
                    index >= 2 ? "hidden sm:flex" : "flex"
                  }`}
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={statusTone(anomaly.severity)}>{anomaly.severity}</Badge>
                      <span className="text-xs font-medium text-ink">{anomaly.category}</span>
                      <span className="text-[11px] text-muted">{anomaly.signal}</span>
                    </div>
                    <p className="mt-1 break-words text-xs text-ink-2">{anomaly.reason}</p>
                  </div>
                  <div className="shrink-0 text-left sm:text-right">
                    <div className="text-sm font-semibold tabular-nums text-ink">
                      {currency(anomaly.cost, anomaly.currency)}
                    </div>
                    <span className="text-[11px] text-accent group-hover:underline">
                      Investigate
                    </span>
                  </div>
                </Link>
              ))}
              {data.anomalies.length > 2 && (
                <Link
                  to="/cost?tab=coverage"
                  className="inline-flex text-xs font-medium text-accent hover:underline"
                >
                  View all {data.anomalies.length} signals
                </Link>
              )}
            </div>
          )}
        </Card>

        <Card>
          <SectionTitle
            title="Biggest movers"
            subtitle={`Selected ${days} days versus the prior ${days} days`}
          />
          <div className="space-y-3">
            {data.movers.slice(0, 5).map((mover, index) => (
              <Link
                key={`${mover.category}-${mover.currency}`}
                to={`/cost?tab=categories&category=${encodeURIComponent(mover.category)}`}
                className={`min-w-0 flex-col items-start gap-1 rounded-lg p-1.5 hover:bg-hairline sm:flex-row sm:items-center sm:justify-between sm:gap-3 ${
                  index >= 3 ? "hidden sm:flex" : "flex"
                }`}
              >
                <div>
                  <div className="text-xs font-medium text-ink">{mover.category}</div>
                  <div className="text-[11px] text-muted">
                    {mover.share_pct}% of {mover.currency} total
                  </div>
                </div>
                <div
                  className={`text-left text-xs font-medium tabular-nums sm:text-right ${
                    mover.change > 0 ? "text-status-serious" : "text-status-good"
                  }`}
                >
                  {mover.change > 0 ? "+" : ""}
                  {currency(mover.change, mover.currency)}
                  <div className="text-[10px] text-muted">
                    {mover.change_pct == null ? "new spend" : percent(mover.change_pct)}
                  </div>
                </div>
              </Link>
            ))}
            {data.movers.length > 3 && (
              <Link
                to="/cost?tab=categories"
                className="inline-flex text-xs font-medium text-accent hover:underline"
              >
                View all movers
              </Link>
            )}
          </div>
        </Card>
      </div>

      <Card>
        <SectionTitle
          title="Data coverage"
          subtitle="Totals stay trustworthy by keeping source basis, currency and freshness explicit"
        />
        <details className="sm:hidden">
          <summary className="cursor-pointer text-xs font-medium text-accent">
            Show source details
          </summary>
          <div className="mt-3">
            <DataHealthList sources={data.data_health} />
          </div>
        </details>
        <div className="hidden sm:block">
          <DataHealthList sources={data.data_health} />
        </div>
      </Card>
    </div>
  );
}

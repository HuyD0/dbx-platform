import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, BrainCircuit, CircleDollarSign, Coins, Gauge, Waypoints } from "lucide-react";
import { useState } from "react";
import { apiGet, isUnavailable } from "../lib/api";
import { compactNum, currency } from "../lib/format";
import type {
  Envelope,
  LlmBreakdown,
  LlmCostPoint,
  LlmCostSummary,
  LlmEfficiency,
  Row,
  SourceHealth,
} from "../lib/types";
import { BarList } from "./BarList";
import { DataTable } from "./DataTable";
import {
  AsOf,
  Badge,
  Card,
  DataHealthList,
  EmptyState,
  ErrorState,
  SectionTitle,
  Skeleton,
  StatTile,
} from "./ui";

function LegacyLlmCost() {
  const costs = useQuery({
    queryKey: ["/api/ml/serving-cost", 30],
    queryFn: () => apiGet<Envelope<Row[]>>("/api/ml/serving-cost", { days: 30 }),
    staleTime: 60_000,
    retry: false,
  });
  const usage = useQuery({
    queryKey: ["/api/ml/token-usage", 30],
    queryFn: () => apiGet<Envelope<Row[]>>("/api/ml/token-usage", { days: 30 }),
    staleTime: 60_000,
    retry: false,
  });

  if (costs.isPending || usage.isPending) return <Skeleton rows={7} />;
  if (costs.isError) return <ErrorState error={costs.error} />;
  if (usage.isError) return <ErrorState error={usage.error} />;

  const grouped = new Map<string, number>();
  for (const row of costs.data.data) {
    const label = [row.billing_origin_product, row.endpoint_name ?? row.sku_name]
      .filter(Boolean)
      .join(" · ");
    grouped.set(label || "Unattributed model serving", (grouped.get(label) ?? 0) + Number(row.list_cost_usd ?? 0));
  }
  const listCost = Array.from(grouped.values()).reduce((sum, value) => sum + value, 0);
  const requests = usage.data.data.reduce((sum, row) => sum + Number(row.request_count ?? row.requests ?? 0), 0);
  const inputTokens = usage.data.data.reduce((sum, row) => sum + Number(row.input_tokens ?? 0), 0);
  const outputTokens = usage.data.data.reduce((sum, row) => sum + Number(row.output_tokens ?? 0), 0);

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-2 rounded-xl border border-status-warning/30 bg-status-warning/5 p-3 text-xs leading-5 text-ink-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-status-warning" />
        Compatibility view: Databricks list cost and serving tokens are shown separately. Azure
        actuals, AI Gateway external spend, cached/reasoning tokens, forecasts and unit economics
        will appear when the canonical LLM ledger is enabled.
      </div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile label="30d model serving" value={currency(listCost, "USD")} hint="Databricks list basis" />
        <StatTile label="Requests observed" value={compactNum(requests)} hint="Serving usage coverage" />
        <StatTile label="Input tokens" value={compactNum(inputTokens)} />
        <StatTile label="Output tokens" value={compactNum(outputTokens)} />
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <SectionTitle title="Spend by model or endpoint" subtitle="Aggregated to avoid duplicate labels" />
          {grouped.size > 0 ? (
            <BarList
              maxBars={12}
              data={Array.from(grouped, ([label, value]) => ({ label, value })).sort(
                (a, b) => b.value - a.value,
              )}
            />
          ) : (
            <EmptyState message="No model-serving cost in the last 30 days." />
          )}
        </Card>
        <Card>
          <SectionTitle title="Token attribution" subtitle="Requester IDs are masked by the backend where required" />
          {usage.data.data.length > 0 ? (
            <DataTable
              rows={usage.data.data}
              pageSize={8}
              exportName="llm-token-usage"
              caption="LLM token usage by endpoint and requester"
            />
          ) : (
            <EmptyState message="No token usage in the last 30 days." />
          )}
        </Card>
      </div>
    </div>
  );
}

export function LlmCostView() {
  const [dimension, setDimension] = useState("all");
  const summary = useQuery({
    queryKey: ["/api/llm-cost/summary"],
    queryFn: () => apiGet<Envelope<LlmCostSummary>>("/api/llm-cost/summary"),
    staleTime: 60_000,
    retry: false,
  });
  const timeseries = useQuery({
    queryKey: ["/api/llm-cost/timeseries"],
    queryFn: () => apiGet<Envelope<LlmCostPoint[]>>("/api/llm-cost/timeseries"),
    staleTime: 60_000,
    retry: false,
  });
  const breakdown = useQuery({
    queryKey: ["/api/llm-cost/breakdown"],
    queryFn: () => apiGet<Envelope<LlmBreakdown[]>>("/api/llm-cost/breakdown"),
    staleTime: 60_000,
    retry: false,
  });
  const efficiency = useQuery({
    queryKey: ["/api/llm-cost/efficiency"],
    queryFn: () => apiGet<Envelope<LlmEfficiency>>("/api/llm-cost/efficiency"),
    staleTime: 60_000,
    retry: false,
  });
  const health = useQuery({
    queryKey: ["/api/llm-cost/data-health"],
    queryFn: () => apiGet<Envelope<{ sources: SourceHealth[] }>>("/api/llm-cost/data-health"),
    staleTime: 60_000,
    retry: false,
  });

  if (summary.isPending) return <Skeleton rows={8} />;
  if (summary.isError && isUnavailable(summary.error)) return <LegacyLlmCost />;
  if (summary.isError) return <ErrorState error={summary.error} />;

  const data = summary.data.data;
  const dimensions = Array.from(new Set(breakdown.data?.data.map((row) => row.dimension) ?? []));
  const breakdownRows =
    dimension === "all"
      ? (breakdown.data?.data ?? [])
      : (breakdown.data?.data ?? []).filter((row) => row.dimension === dimension);
  const forecast = data.forecast;
  const sources = health.data?.data.sources ?? data.coverage ?? [];
  const tokens =
    Number(data.input_tokens ?? 0) +
    Number(data.output_tokens ?? 0) +
    Number(data.cached_tokens ?? 0) +
    Number(data.reasoning_tokens ?? 0);
  const singleTotal = data.totals.length === 1 ? data.totals[0] : undefined;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-2">
          {data.totals.map((total) => (
            <Badge key={`${total.currency}-${total.basis}`} tone="info">
              {total.basis.replaceAll("_", " ")} · {total.currency}
            </Badge>
          ))}
          {data.totals.length > 1 && (
            <span className="text-[11px] text-muted">Ledgers are not silently combined.</span>
          )}
        </div>
        <AsOf
          asOf={summary.data.as_of}
          cached={summary.data.cached}
          onRefresh={() => {
            void Promise.all([
              summary.refetch(),
              timeseries.refetch(),
              breakdown.refetch(),
              efficiency.refetch(),
              health.refetch(),
            ]);
          }}
          refreshing={
            summary.isFetching ||
            timeseries.isFetching ||
            breakdown.isFetching ||
            efficiency.isFetching ||
            health.isFetching
          }
        />
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <StatTile
          label={singleTotal ? "MTD LLM spend" : "MTD cost ledgers"}
          value={
            singleTotal
              ? currency(singleTotal.cost, singleTotal.currency)
              : data.totals.length
                ? `${data.totals.length} separate totals`
                : "—"
          }
          hint={
            singleTotal
              ? `${singleTotal.basis.replaceAll("_", " ")} · ${singleTotal.currency}${
                  singleTotal.period_delta_pct == null
                    ? ""
                    : ` · ${singleTotal.period_delta_pct >= 0 ? "+" : ""}${singleTotal.period_delta_pct}% vs aligned prior month`
                }`
              : data.totals.length
                ? "Currencies and cost bases are shown separately below"
                : "No covered cost"
          }
        />
        <StatTile label="Requests" value={compactNum(data.requests)} />
        <StatTile label="Observed tokens" value={compactNum(tokens)} hint="input + output + cached + reasoning" />
        <StatTile
          label="Cost / request"
          value={
            data.cost_per_request != null && singleTotal
              ? currency(data.cost_per_request, singleTotal.currency)
              : "—"
          }
        />
        <StatTile
          label="Month-end forecast"
          value={
            forecast?.month_end != null
              ? currency(forecast.month_end, forecast.currency ?? singleTotal?.currency)
              : "—"
          }
          hint={
            forecast?.lower != null && forecast.upper != null
              ? `${currency(forecast.lower, forecast.currency)}–${currency(forecast.upper, forecast.currency)}`
              : "Forecast not ready"
          }
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.25fr_0.75fr]">
        <Card>
          <SectionTitle
            title="LLM cost ledger"
            subtitle="Provider and model detail with explicit currency and cost basis"
            right={<CircleDollarSign className="h-4 w-4 text-accent" />}
          />
          {timeseries.isPending ? (
            <Skeleton rows={5} />
          ) : timeseries.isError ? (
            <ErrorState error={timeseries.error} />
          ) : timeseries.data.data.length > 0 ? (
            <DataTable
              rows={timeseries.data.data}
              exportName="llm-cost-timeseries"
              caption="LLM daily cost and usage"
            />
          ) : (
            <EmptyState message="No normalized LLM costs in this period." />
          )}
        </Card>
        <Card>
          <SectionTitle
            title="Coverage"
            subtitle="Freshness, retention and preview status by source"
            right={<Waypoints className="h-4 w-4 text-accent" />}
          />
          <DataHealthList sources={sources} />
        </Card>
      </div>

      <Card>
        <SectionTitle
          title="Attribution"
          subtitle="Provider, model, endpoint, app, team and principal without cross-basis aggregation"
          right={
            dimensions.length > 0 ? (
              <label className="flex items-center gap-2 text-xs text-muted">
                Dimension
                <select
                  value={dimension}
                  onChange={(event) => setDimension(event.target.value)}
                  className="rounded-lg border border-grid bg-page px-2 py-1 text-ink"
                >
                  <option value="all">All</option>
                  {dimensions.map((value) => (
                    <option key={value} value={value}>
                      {value.replaceAll("_", " ")}
                    </option>
                  ))}
                </select>
              </label>
            ) : undefined
          }
        />
        {breakdown.isPending ? (
          <Skeleton rows={5} />
        ) : breakdown.isError ? (
          <ErrorState error={breakdown.error} />
        ) : breakdownRows.length > 0 ? (
          <DataTable
            rows={breakdownRows}
            exportName="llm-cost-attribution"
            caption="LLM cost attribution"
          />
        ) : (
          <EmptyState
            message="Attribution will appear after provider usage is normalized."
            positive={false}
          />
        )}
      </Card>

      <Card>
        <SectionTitle
          title="Efficiency and value"
          subtitle="Retries, latency, cache use and cost per successful outcome"
          right={<Gauge className="h-4 w-4 text-accent" />}
        />
        {efficiency.isPending ? (
          <Skeleton rows={4} />
        ) : efficiency.isError ? (
          <ErrorState error={efficiency.error} />
        ) : (
          <div className="space-y-4">
            {efficiency.data.data.metrics &&
              Object.keys(efficiency.data.data.metrics).length > 0 && (
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  {Object.entries(efficiency.data.data.metrics).map(([label, value]) => (
                    <Card key={label} className="!rounded-xl !p-3">
                      <div className="text-[11px] text-muted">{label.replaceAll("_", " ")}</div>
                      <div className="mt-1 text-base font-semibold text-ink">
                        {typeof value === "number" ? compactNum(value) : String(value ?? "—")}
                      </div>
                    </Card>
                  ))}
                </div>
              )}
            {(efficiency.data.data.recommendations ?? []).length > 0 ? (
              <DataTable
                rows={efficiency.data.data.recommendations ?? []}
                exportName="llm-efficiency-recommendations"
                caption="LLM efficiency recommendations"
              />
            ) : (
              <EmptyState message="No LLM efficiency anomaly needs attention." />
            )}
          </div>
        )}
      </Card>

      <p className="flex items-center gap-1.5 text-[11px] text-muted">
        <BrainCircuit className="h-3.5 w-3.5" />
        Request telemetry allocates covered billed totals; it is never presented as invoice-accurate
        cost when the provider did not supply one.
      </p>
      {data.cost_per_million_tokens != null && singleTotal && (
        <p className="flex items-center gap-1.5 text-[11px] text-muted">
          <Coins className="h-3.5 w-3.5" />
          Blended observed cost per 1M tokens:{" "}
          {currency(data.cost_per_million_tokens, singleTotal.currency)} ({singleTotal.basis})
        </p>
      )}
    </div>
  );
}

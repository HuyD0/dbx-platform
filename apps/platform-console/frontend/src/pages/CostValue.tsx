import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Info, Layers3 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { BudgetPlanButton } from "../components/BudgetPlanButton";
import { COST_TABS, CostTabs } from "../components/CostTabs";
import { CostTrendChart } from "../components/CostTrendChart";
import { DataTable } from "../components/DataTable";
import { FindingsSection } from "../components/FindingsSection";
import { LlmCostView } from "../components/LlmCostView";
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
} from "../components/ui";
import { apiGet } from "../lib/api";
import { currency } from "../lib/format";
import type {
  BillingAlignmentRow,
  CostOverview,
  Envelope,
  Row,
} from "../lib/types";
import { Cost } from "./Cost";

const ALIGNMENT_FILTERS = [
  "ALL",
  "AZURE_ONLY",
  "DATABRICKS_ONLY",
  "BILLING_LAG",
  "PATTERN_VARIANCE",
  "MONETARY_VARIANCE",
  "BASIS_MISMATCH",
] as const;

function alignmentLabel(value: string) {
  return value
    .toLowerCase()
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function BillingAlignment({ data }: { data: CostOverview }) {
  const [filter, setFilter] =
    useState<(typeof ALIGNMENT_FILTERS)[number]>("ALL");
  const query = useQuery({
    queryKey: ["/api/cost/reconciliation", data.period.days],
    queryFn: () =>
      apiGet<Envelope<BillingAlignmentRow[]>>(
        `/api/cost/reconciliation?days=${data.period.days}`,
      ),
    staleTime: 60_000,
    retry: false,
  });
  const summary = data.billing_alignment;
  const rows = query.data?.data ?? [];
  const filtered =
    filter === "ALL"
      ? rows
      : rows.filter((row) => row.classifications?.includes(filter));
  const statusTone =
    summary.status === "aligned"
      ? "good"
      : summary.status === "unavailable"
        ? "info"
        : "warning";

  return (
    <div className="space-y-4">
      <Card>
        <SectionTitle
          title="Variance watch"
          subtitle="Daily and SKU-family alignment without treating CAD actuals and USD list price as the same money"
          right={<Badge tone={statusTone}>{alignmentLabel(summary.status)}</Badge>}
        />
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <div className="min-w-0 rounded-xl border border-grid bg-page/25 p-3">
            <p className="text-[11px] text-muted">Variance rows</p>
            <p className="mt-1 text-2xl font-semibold tabular-nums text-ink">
              {summary.variance_count}
            </p>
            <p className="mt-1 text-[11px] text-muted">
              {summary.unmatched_count} unmatched
            </p>
          </div>
          <div className="min-w-0 rounded-xl border border-grid bg-page/25 p-3">
            <p className="text-[11px] text-muted">Azure Actual</p>
            <p className="mt-1 break-words text-base font-semibold tabular-nums text-ink">
              {summary.azure_totals.length
                ? summary.azure_totals
                    .map((total) => currency(total.cost, total.currency))
                    .join(" · ")
                : "—"}
            </p>
            <p className="mt-1 text-[11px] text-muted">
              through {summary.latest_azure_date ?? "—"}
            </p>
          </div>
          <div className="min-w-0 rounded-xl border border-grid bg-page/25 p-3">
            <p className="text-[11px] text-muted">Databricks List</p>
            <p className="mt-1 break-words text-base font-semibold tabular-nums text-ink">
              {summary.databricks_totals.length
                ? summary.databricks_totals
                    .map((total) => currency(total.cost, total.currency))
                    .join(" · ")
                : "—"}
            </p>
            <p className="mt-1 text-[11px] text-muted">
              through {summary.latest_databricks_date ?? "—"}
            </p>
          </div>
          <div className="min-w-0 rounded-xl border border-grid bg-page/25 p-3">
            <p className="text-[11px] text-muted">Largest pattern difference</p>
            <p className="mt-1 text-base font-semibold tabular-nums text-ink">
              {summary.largest_pattern_variance
                ? `${Math.abs(summary.largest_pattern_variance.delta_pct_points)} pp`
                : "—"}
            </p>
            <p className="mt-1 break-words text-[11px] text-muted">
              {summary.largest_pattern_variance
                ? `${summary.largest_pattern_variance.sku_family} · ${summary.largest_pattern_variance.usage_date}`
                : "No material spend-shape difference"}
            </p>
          </div>
        </div>
        <p className="mt-3 text-xs text-muted">{summary.notes}</p>
      </Card>

      <Card>
        <SectionTitle
          title="Where variance exists"
          subtitle="Filter the closed-day register; open or delayed source days are not treated as monetary variance"
        />
        <div
          className="mb-3 flex flex-wrap gap-1.5"
          aria-label="Billing alignment filters"
        >
          {ALIGNMENT_FILTERS.map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => setFilter(option)}
              aria-pressed={filter === option}
              className={`rounded-lg px-2.5 py-1.5 text-[11px] font-medium ${
                filter === option
                  ? "bg-accent text-white"
                  : "border border-grid text-ink-2 hover:bg-hairline"
              }`}
            >
              {alignmentLabel(option)}
            </button>
          ))}
        </div>
        {query.isPending ? (
          <Skeleton rows={6} />
        ) : query.isError ? (
          <ErrorState error={query.error} />
        ) : filtered.length === 0 ? (
          <EmptyState message="No reconciliation rows match this filter." />
        ) : (
          <div className="space-y-2" aria-label="Billing variance register">
            {filtered.map((row, index) => (
              <article
                key={`${row.usage_date}-${row.sku_family}-${index}`}
                className="min-w-0 rounded-xl border border-grid bg-page/20 p-3"
              >
                <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Badge
                        tone={
                          row.comparison_status === "MATCHED"
                            ? "good"
                            : row.comparison_status === "OPEN_PERIOD"
                              ? "info"
                              : "warning"
                        }
                      >
                        {alignmentLabel(row.comparison_status)}
                      </Badge>
                      <span className="text-xs font-semibold text-ink">
                        {row.sku_family}
                      </span>
                      <span className="text-[11px] text-muted">{row.usage_date}</span>
                    </div>
                    <p className="mt-1 break-words text-[11px] text-muted">
                      {(row.classifications ?? []).map(alignmentLabel).join(" · ")}
                    </p>
                  </div>
                  {row.pattern_delta_pct_points != null && (
                    <div className="text-left sm:text-right">
                      <p className="text-sm font-semibold tabular-nums text-ink">
                        {row.pattern_delta_pct_points > 0 ? "+" : ""}
                        {row.pattern_delta_pct_points} pp
                      </p>
                      <p className="text-[10px] text-muted">Azure share − list share</p>
                    </div>
                  )}
                </div>
                <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                  <div className="min-w-0 rounded-lg bg-hairline/35 p-2">
                    <p className="text-[10px] uppercase tracking-wide text-muted">
                      Azure Actual
                    </p>
                    <p className="mt-0.5 break-words font-medium tabular-nums text-ink">
                      {row.azure_billed_cost == null
                        ? "No row"
                        : currency(row.azure_billed_cost, row.azure_currency)}
                    </p>
                    <p className="mt-1 break-words text-[10px] text-muted">
                      {(row.azure_meters ?? []).join(", ") || "No Azure meter"}
                    </p>
                  </div>
                  <div className="min-w-0 rounded-lg bg-hairline/35 p-2">
                    <p className="text-[10px] uppercase tracking-wide text-muted">
                      Databricks List
                    </p>
                    <p className="mt-0.5 break-words font-medium tabular-nums text-ink">
                      {row.databricks_list_usd == null
                        ? "No row"
                        : currency(row.databricks_list_usd, "USD")}
                    </p>
                    <p className="mt-1 break-words text-[10px] text-muted">
                      {(row.databricks_skus ?? []).join(", ") || "No Databricks SKU"}
                    </p>
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

function Budgets() {
  const query = useQuery({
    queryKey: ["/api/llm-cost/budgets"],
    queryFn: () => apiGet<Envelope<Row[]>>("/api/llm-cost/budgets"),
    staleTime: 60_000,
    retry: false,
  });
  return (
    <div className="space-y-4">
      <Card>
        <SectionTitle
          title="Cost guardrails"
          subtitle="Alerts at 80% and 100% never change resources automatically"
          right={<BudgetPlanButton label="Plan budget change" />}
        />
        {query.isPending ? (
          <Skeleton rows={4} />
        ) : query.isError ? (
          <ErrorState error={query.error} />
        ) : query.data.data.length > 0 ? (
          <>
            <AsOf
              asOf={query.data.as_of}
              cached={query.data.cached}
              onRefresh={() => query.refetch()}
              refreshing={query.isFetching}
            />
            <div className="mt-3">
              <DataTable
                rows={query.data.data}
                exportName="cost-budgets"
                caption="Configured cost budgets"
              />
            </div>
          </>
        ) : (
          <EmptyState
            message="No budget is configured. Plan a provider, team or use-case budget above."
            positive={false}
          />
        )}
      </Card>
      <FindingsSection
        title="Platform forecast"
        subtitle="Month-end outlook with explicit currency and cost basis"
        path="/api/cost/forecast"
        emptyMessage="No current platform forecast is ready."
      />
    </div>
  );
}

function CategoryExplorer({
  data,
  selectedDate,
  selectedCategory,
}: {
  data: CostOverview;
  selectedDate: string | null;
  selectedCategory: string | null;
}) {
  const navigate = useNavigate();
  const currencyCode = data.totals[0]?.currency;
  const points = data.series.filter(
    (point) =>
      (!currencyCode || point.currency === currencyCode)
      && (!selectedCategory || point.category === selectedCategory),
  );
  const categories = data.categories.filter(
    (row) => !currencyCode || row.currency === currencyCode,
  );
  const dayRows = selectedDate
    ? data.series.filter(
        (point) =>
          point.usage_date === selectedDate
          && (!currencyCode || point.currency === currencyCode),
      )
    : [];

  return (
    <div className="space-y-4">
      {(selectedDate || selectedCategory) && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-accent/25 bg-accent/5 p-3 text-xs text-ink-2">
          <span className="font-medium text-ink">Active filter:</span>
          {selectedDate && <Badge tone="info">{selectedDate}</Badge>}
          {selectedCategory && <Badge tone="info">{selectedCategory}</Badge>}
          <Link to="/cost?tab=categories" className="ml-auto text-accent hover:underline">
            Clear filters
          </Link>
        </div>
      )}
      <Card>
        <SectionTitle
          title={selectedCategory ? `${selectedCategory} trend` : "Cost trend by category"}
          subtitle="Azure billed actuals; click a day to open its category breakdown"
        />
        <CostTrendChart
          points={points}
          onSelectDate={(date) =>
            navigate(
              `/cost?tab=categories&date=${date}${
                selectedCategory ? `&category=${encodeURIComponent(selectedCategory)}` : ""
              }`,
            )
          }
        />
      </Card>
      {selectedDate && (
        <Card>
          <SectionTitle
            title={`What contributed on ${selectedDate}`}
            subtitle="Click a category from the table below to isolate its trend"
          />
          {dayRows.length ? (
            <DataTable
              rows={dayRows}
              columns={["category", "cost", "currency", "cost_basis"]}
              caption={`Cost categories for ${selectedDate}`}
              exportName={`cost-${selectedDate}`}
              rowAction={(row) => (
                <Link
                  to={`/cost?tab=categories&date=${selectedDate}&category=${encodeURIComponent(
                    String(row.category),
                  )}`}
                  className="text-xs font-medium text-accent hover:underline"
                >
                  Isolate
                </Link>
              )}
              rowActionLabel="Inspect"
            />
          ) : (
            <EmptyState message="No cost rows match this day and category." positive={false} />
          )}
        </Card>
      )}
      <div className="grid gap-4 xl:grid-cols-[1.25fr_1fr]">
        <Card>
          <SectionTitle
            title="Service categories"
            subtitle="Stable categories prevent Azure service-name changes from hiding trends"
          />
          <DataTable
            rows={categories}
            columns={["category", "cost", "currency", "share_pct", "cost_basis"]}
            caption="Azure actual cost by service category"
            exportName="cost-by-category"
            rowAction={(row) => (
              <Link
                to={`/cost?tab=categories&category=${encodeURIComponent(
                  String(row.category),
                )}`}
                className="text-xs font-medium text-accent hover:underline"
              >
                Trend
              </Link>
            )}
            rowActionLabel="Inspect"
          />
        </Card>
        <Card>
          <SectionTitle
            title="Period movers"
            subtitle="Current selected window compared with the immediately prior window"
          />
          <DataTable
            rows={data.movers}
            columns={[
              "category",
              "cost",
              "previous_cost",
              "change",
              "change_pct",
              "currency",
            ]}
            caption="Cost movers"
            exportName="cost-movers"
            pageSize={8}
          />
        </Card>
      </div>
    </div>
  );
}

function DatabricksDrivers({ data }: { data: CostOverview }) {
  const driver = data.databricks_list;
  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-xs font-medium text-muted">
              <Layers3 className="h-4 w-4 text-series-1" />
              Databricks workload signal
            </div>
            <div className="mt-2 text-3xl font-semibold tabular-nums text-ink">
              {currency(driver.cost, driver.currency)}
            </div>
            <p className="mt-1 text-xs text-muted">{driver.cost_basis}</p>
          </div>
          <Badge tone={driver.status === "healthy" ? "good" : "warning"}>
            {driver.status}
          </Badge>
        </div>
        <div className="mt-4 flex gap-2 rounded-xl border border-grid bg-hairline/20 p-3">
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
          <p className="text-xs leading-5 text-ink-2">
            This is list-price attribution, not another bill. It explains which Databricks
            SKUs and workloads drove the Azure Databricks actual cost, so it is never added
            to the platform total.
          </p>
        </div>
      </Card>
      {driver.rows.length > 0 && (
        <Card>
          <SectionTitle
            title="SKU attribution"
            subtitle="Workspace-scoped DBU and list-price signal"
          />
          <DataTable
            rows={driver.rows}
            caption="Databricks SKU cost attribution"
            exportName="databricks-cost-drivers"
          />
        </Card>
      )}
      <Cost />
    </div>
  );
}

function OwnershipExplorer({ data }: { data: CostOverview }) {
  const [dimension, setDimension] = useState("team");
  const [azureDimension, setAzureDimension] = useState("service");
  const dimensions = [
    { id: "team", label: "Team" },
    { id: "project", label: "Project" },
    { id: "workspace", label: "Workspace" },
  ];
  const azureDimensions = [
    { id: "service", label: "Service" },
    { id: "resource-group", label: "Resource group" },
    { id: "resource", label: "Resource" },
    { id: "meter", label: "Meter" },
  ];
  return (
    <div className="space-y-4">
      <Card>
        <SectionTitle
          title="Azure actual cost"
          subtitle="Exact Cost Management actuals by service, resource group, resource, or meter"
        />
        <div
          className="mb-3 flex flex-wrap items-center gap-1"
          role="group"
          aria-label="Azure cost dimension"
        >
          {azureDimensions.map((option) => (
            <button
              key={option.id}
              type="button"
              onClick={() => setAzureDimension(option.id)}
              aria-pressed={azureDimension === option.id}
              className={`rounded-lg px-2.5 py-1 text-xs font-medium ${
                azureDimension === option.id
                  ? "bg-accent text-white"
                  : "border border-grid text-ink-2 hover:bg-hairline"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
        <FindingsSection
          title={`Azure billed actuals by ${azureDimension}`}
          path="/api/cost/azure"
          params={{ by: azureDimension, days: data.period.days }}
          emptyMessage="No Azure billing rows are available in this window."
        />
      </Card>
      <Card>
        <SectionTitle
          title="Databricks tag attribution"
          subtitle="Team and project tags explain workload ownership without changing the Azure total"
        />
        <div className="mb-3 flex flex-wrap items-center gap-1" role="group" aria-label="Attribution dimension">
          {dimensions.map((option) => (
            <button
              key={option.id}
              type="button"
              onClick={() => setDimension(option.id)}
              aria-pressed={dimension === option.id}
              className={`rounded-lg px-2.5 py-1 text-xs font-medium ${
                dimension === option.id
                  ? "bg-accent text-white"
                  : "border border-grid text-ink-2 hover:bg-hairline"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
        <FindingsSection
          title={`List-cost attribution by ${dimension}`}
          path="/api/cost/attribution"
          params={{ dimension, days: data.period.days }}
          emptyMessage="No tagged Databricks usage is available in this window."
        />
      </Card>
    </div>
  );
}

export function CostValue() {
  const [params] = useSearchParams();
  const requested = params.get("tab") ?? "categories";
  const active = COST_TABS.some((tab) => tab.id === requested) ? requested : "categories";
  const days = Math.max(1, Math.min(365, Number(params.get("days") ?? 30)));
  const query = useQuery({
    queryKey: ["/api/cost/overview", days],
    queryFn: () => apiGet<Envelope<CostOverview>>(`/api/cost/overview?days=${days}`),
    staleTime: 60_000,
    retry: false,
  });
  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="FinOps"
        title="Cost Explorer"
        description="Move from the bill to its service, ownership and Databricks workload drivers without mixing cost bases."
        actions={
          <Link
            to="/"
            className="inline-flex items-center gap-1.5 rounded-lg border border-grid px-3 py-2 text-xs font-medium text-ink-2 hover:bg-hairline"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> Cost Control
          </Link>
        }
      />
      <CostTabs />
      {query.isPending ? (
        <Skeleton rows={10} />
      ) : query.isError ? (
        <ErrorState error={query.error} />
      ) : (
        <div role="tabpanel" className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
            <span>
              {query.data.data.scope.label} · {query.data.data.period.from ?? "—"} to{" "}
              {query.data.data.period.to ?? "—"}
            </span>
            <AsOf
              asOf={query.data.as_of}
              cached={query.data.cached}
              onRefresh={() => query.refetch()}
              refreshing={query.isFetching}
            />
          </div>
          {active === "categories" && (
            <CategoryExplorer
              data={query.data.data}
              selectedDate={params.get("date")}
              selectedCategory={params.get("category")}
            />
          )}
          {active === "databricks" && (
            <DatabricksDrivers data={query.data.data} />
          )}
          {active === "ownership" && <OwnershipExplorer data={query.data.data} />}
          {active === "alignment" && <BillingAlignment data={query.data.data} />}
          {active === "forecast" && <Budgets />}
          {active === "coverage" && (
            <div className="space-y-4">
              <Card>
                <SectionTitle
                  title="Source coverage"
                  subtitle="Freshness, retention and attribution health for every cost claim"
                />
                <DataHealthList sources={query.data.data.data_health} />
              </Card>
              <Card>
                <SectionTitle
                  title="Detected runaway signals"
                  subtitle="Daily spikes and sustained 7-day acceleration"
                />
                {query.data.data.anomalies.length > 0 ? (
                  <DataTable
                    rows={query.data.data.anomalies}
                    columns={[
                      "severity",
                      "signal",
                      "day",
                      "category",
                      "cost",
                      "baseline",
                      "change_pct",
                      "currency",
                    ]}
                    caption="Cost anomaly signals"
                    exportName="cost-anomalies"
                    rowAction={(row) => (
                      <Link
                        to={`/cost/anomalies/${encodeURIComponent(String(row.id))}?days=${days}`}
                        className="text-xs font-medium text-accent hover:underline"
                      >
                        Investigate
                      </Link>
                    )}
                    rowActionLabel="Inspect"
                  />
                ) : (
                  <EmptyState message="No runaway signal crossed the configured thresholds." />
                )}
              </Card>
            </div>
          )}
          {active === "llm" && <LlmCostView />}
        </div>
      )}
    </div>
  );
}

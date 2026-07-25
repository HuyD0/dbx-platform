import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Info, Layers3 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { BudgetPlanButton } from "../components/BudgetPlanButton";
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
  Tabs,
} from "../components/ui";
import { apiGet } from "../lib/api";
import { currency } from "../lib/format";
import type { CostOverview, Envelope, Row } from "../lib/types";
import { Cost } from "./Cost";

const COST_TABS = [
  { id: "categories", label: "Service categories" },
  { id: "databricks", label: "Databricks drivers" },
  { id: "ownership", label: "Ownership" },
  { id: "forecast", label: "Forecast & budgets" },
  { id: "coverage", label: "Coverage" },
  { id: "llm", label: "LLM detail" },
];

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
  const dimensions = [
    { id: "team", label: "Team" },
    { id: "project", label: "Project" },
    { id: "workspace", label: "Workspace" },
  ];
  return (
    <div className="space-y-4">
      <Card>
        <SectionTitle
          title="Azure ownership allocation"
          subtitle="Resource group is the current bill-of-record fallback"
        />
        <DataTable
          rows={data.ownership}
          columns={["owner", "cost", "currency", "share_pct", "cost_basis"]}
          caption="Azure actual cost by owner"
          exportName="azure-cost-by-owner"
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
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab") ?? "categories";
  const active = COST_TABS.some((tab) => tab.id === requested) ? requested : "categories";
  const days = Math.max(1, Math.min(365, Number(params.get("days") ?? 30)));
  const query = useQuery({
    queryKey: ["/api/cost/overview", days],
    queryFn: () => apiGet<Envelope<CostOverview>>(`/api/cost/overview?days=${days}`),
    staleTime: 60_000,
    retry: false,
  });
  const setActive = (tab: string) => {
    const next = new URLSearchParams(params);
    if (tab === "categories") next.delete("tab");
    else next.set("tab", tab);
    next.delete("date");
    next.delete("category");
    setParams(next, { replace: true });
  };

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
      <Tabs tabs={COST_TABS} active={active} onChange={setActive} label="Cost Explorer views" />
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

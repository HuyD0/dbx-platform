import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { BudgetPlanButton } from "../components/BudgetPlanButton";
import { DataTable } from "../components/DataTable";
import { FindingsSection } from "../components/FindingsSection";
import { LlmCostView } from "../components/LlmCostView";
import {
  AsOf,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  SectionTitle,
  Skeleton,
  Tabs,
} from "../components/ui";
import { apiGet } from "../lib/api";
import type { Envelope, Row } from "../lib/types";
import { Cost } from "./Cost";

const COST_TABS = [
  { id: "databricks", label: "Databricks" },
  { id: "azure", label: "Azure" },
  { id: "llm", label: "LLM & AI" },
  { id: "budgets", label: "Budgets & forecasts" },
];

function AzureCost() {
  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-grid bg-hairline/20 p-3 text-xs leading-5 text-ink-2">
        Azure amounts are billed actuals from Cost Management. They remain separate from
        Databricks list cost and expose their original currency.
      </div>
      <FindingsSection
        title="Azure actual cost"
        subtitle="Daily spend by service, resource, meter and allocation tags"
        path="/api/cost/azure"
        params={{ days: 30 }}
        emptyMessage="No Azure billing rows in the last 30 days."
      />
      <FindingsSection
        title="Azure spend anomalies"
        subtitle="Material deviations from the trailing baseline"
        path="/api/cost/azure-anomalies"
        params={{ days: 30 }}
        emptyMessage="No Azure cost anomaly needs attention."
      />
      <FindingsSection
        title="Azure cost forecast"
        subtitle="P10, P50 and P90 by service bucket with model freshness"
        path="/api/cost/azure-forecast"
        params={{ days: 30 }}
        emptyMessage="No current Azure forecast is available."
      />
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
        title="Consolidated forecast"
        subtitle="Month-end outlook with explicit currency and cost basis"
        path="/api/cost/forecast"
        emptyMessage="No consolidated forecast is ready."
      />
    </div>
  );
}

export function CostValue() {
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab") ?? "databricks";
  const active = COST_TABS.some((tab) => tab.id === requested) ? requested : "databricks";
  const setActive = (tab: string) => {
    const next = new URLSearchParams(params);
    if (tab === "databricks") next.delete("tab");
    else next.set("tab", tab);
    setParams(next, { replace: true });
  };

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="FinOps"
        title="Cost & Value"
        description="Understand what was billed, why it changed, and what useful outcome each dollar produced."
      />
      <Tabs tabs={COST_TABS} active={active} onChange={setActive} label="Cost and value views" />
      <div role="tabpanel">
        {active === "databricks" && <Cost />}
        {active === "azure" && <AzureCost />}
        {active === "llm" && <LlmCostView />}
        {active === "budgets" && <Budgets />}
      </div>
    </div>
  );
}

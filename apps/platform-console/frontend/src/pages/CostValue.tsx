import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
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
          title="Mission Control cost guardrails"
          subtitle="Workspace evidence budgets with approval-gated changes; these are separate from Databricks account budgets"
          right={<BudgetPlanButton label="Plan Mission Control budget" />}
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
            message="No Mission Control budget is configured. Plan a workspace, provider, team or use-case budget above."
            positive={false}
          />
        )}
      </Card>
      <Card>
        <SectionTitle
          title="Databricks account budgets"
          subtitle="Native account-console budgets use Databricks list price in USD and require an account admin"
        />
        <p className="text-xs leading-5 text-ink-2">
          Native Databricks budgets are account-level objects, while this app currently has a
          workspace-scoped identity and approval ledger. Create or inspect the native budget in
          the Azure Databricks account console; Mission Control will continue to show its own
          currency- and cost-basis-specific guardrails above.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <a
            href="https://accounts.azuredatabricks.net/"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 rounded-lg border border-grid px-3 py-1.5 text-xs font-medium text-ink hover:bg-hairline"
          >
            Open account console <ExternalLink className="h-3.5 w-3.5" />
          </a>
          <a
            href="https://learn.microsoft.com/azure/databricks/admin/account-settings/budgets"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 rounded-lg border border-grid px-3 py-1.5 text-xs font-medium text-ink hover:bg-hairline"
          >
            Azure budget documentation <ExternalLink className="h-3.5 w-3.5" />
          </a>
        </div>
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

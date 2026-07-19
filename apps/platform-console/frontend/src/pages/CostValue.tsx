import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { useState } from "react";
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
  { id: "attribution", label: "Attribution" },
  { id: "azure", label: "Azure & Foundry" },
  { id: "llm", label: "LLM & AI" },
  { id: "budgets", label: "Budgets & forecasts" },
];

const ATTRIBUTION_DIMENSIONS = [
  { id: "team", label: "Team" },
  { id: "project", label: "Project" },
  { id: "workspace", label: "Workspace" },
];

const ATTRIBUTION_WINDOWS = [7, 30, 90];

function Attribution() {
  const [dimension, setDimension] = useState("team");
  const [days, setDays] = useState(30);
  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-grid bg-hairline/20 p-3 text-xs leading-5 text-ink-2">
        Attribution reads the <code>team</code>/<code>project</code> tags the cluster policies
        enforce. Rows labeled <code>unallocated</code> carry no tag — tighten enforcement in
        Data Governance to shrink them. Column names follow FOCUS vocabulary.
      </div>
      <div className="flex flex-wrap items-center gap-4 text-xs">
        <div className="flex items-center gap-1" role="group" aria-label="Attribution dimension">
          <span className="mr-1 text-muted">Attribute by:</span>
          {ATTRIBUTION_DIMENSIONS.map((d) => (
            <button
              key={d.id}
              type="button"
              onClick={() => setDimension(d.id)}
              aria-pressed={dimension === d.id}
              className={`rounded-lg px-2.5 py-1 font-medium ${
                dimension === d.id
                  ? "bg-accent text-white"
                  : "border border-grid text-ink-2 hover:bg-hairline"
              }`}
            >
              {d.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1" role="group" aria-label="Attribution window">
          <span className="mr-1 text-muted">Window:</span>
          {ATTRIBUTION_WINDOWS.map((w) => (
            <button
              key={w}
              type="button"
              onClick={() => setDays(w)}
              aria-pressed={days === w}
              className={`rounded-lg px-2.5 py-1 font-medium ${
                days === w
                  ? "bg-accent text-white"
                  : "border border-grid text-ink-2 hover:bg-hairline"
              }`}
            >
              {w}d
            </button>
          ))}
        </div>
      </div>
      <FindingsSection
        title={`Spend by ${dimension}`}
        subtitle={`Databricks list cost attributed by the ${dimension} dimension, last ${days} days`}
        path="/api/cost/attribution"
        params={{ dimension, days }}
        emptyMessage="No billed usage in the window."
      />
    </div>
  );
}

const AZURE_WINDOWS = [7, 30, 90];

const AZURE_DIMENSIONS = [
  { id: "service", label: "Service" },
  { id: "bucket", label: "Bucket" },
  { id: "resource-group", label: "Resource group" },
];

const AZURE_DIMENSION_SUBTITLES: Record<string, string> = {
  service: "Billed actuals per Azure service — Azure OpenAI appears under Cognitive Services",
  bucket:
    "Billed actuals per allocation bucket — foundry_ai isolates Azure OpenAI, AI Foundry and Azure ML spend",
  "resource-group": "Billed actuals per resource group for chargeback",
};

function ForecastBySeries({ rows }: { rows: Row[] }) {
  const bySeries = new Map<string, Row[]>();
  for (const row of rows) {
    const key = String(row.series ?? "total");
    bySeries.set(key, [...(bySeries.get(key) ?? []), row]);
  }
  const ordered = [...bySeries.keys()].sort((a, b) => {
    if (a === "total") return -1;
    if (b === "total") return 1;
    return a.localeCompare(b);
  });
  return (
    <div className="space-y-4">
      {ordered.map((series) => (
        <div key={series}>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-2">
            {series}
          </h3>
          <DataTable
            rows={(bySeries.get(series) ?? []).map(({ series: _series, ...rest }) => rest)}
            caption={`Forecast for the ${series} series`}
            exportName={`azure-forecast-${series}`}
          />
        </div>
      ))}
    </div>
  );
}

function AzureCost() {
  const [days, setDays] = useState(30);
  const [by, setBy] = useState("service");
  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-grid bg-hairline/20 p-3 text-xs leading-5 text-ink-2">
        Azure amounts are billed actuals from Cost Management. They remain separate from
        Databricks list cost and expose their original currency.
      </div>
      <div className="flex flex-wrap items-center gap-4 text-xs">
        <div className="flex items-center gap-1" role="group" aria-label="Azure cost window">
          <span className="mr-1 text-muted">Window:</span>
          {AZURE_WINDOWS.map((w) => (
            <button
              key={w}
              type="button"
              onClick={() => setDays(w)}
              aria-pressed={days === w}
              className={`rounded-lg px-2.5 py-1 font-medium ${
                days === w
                  ? "bg-accent text-white"
                  : "border border-grid text-ink-2 hover:bg-hairline"
              }`}
            >
              {w}d
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1" role="group" aria-label="Azure cost dimension">
          <span className="mr-1 text-muted">Group by:</span>
          {AZURE_DIMENSIONS.map((dimension) => (
            <button
              key={dimension.id}
              type="button"
              onClick={() => setBy(dimension.id)}
              aria-pressed={by === dimension.id}
              className={`rounded-lg px-2.5 py-1 font-medium ${
                by === dimension.id
                  ? "bg-accent text-white"
                  : "border border-grid text-ink-2 hover:bg-hairline"
              }`}
            >
              {dimension.label}
            </button>
          ))}
        </div>
      </div>
      <FindingsSection
        title="Azure actual cost"
        subtitle={`${AZURE_DIMENSION_SUBTITLES[by]}, last ${days} days`}
        path="/api/cost/azure"
        params={{ days, by }}
        emptyMessage={`No Azure billing rows in the last ${days} days.`}
      />
      <FindingsSection
        title="Foundry deployment drill"
        subtitle={`Azure OpenAI / AI Foundry actuals per billing meter (per-model attribution), last ${days} days`}
        path="/api/cost/azure-detail"
        params={{ by: "meter", bucket: "foundry_ai", days }}
        emptyMessage="No Foundry-bucket detail rows — the azure-cost pull populates azure_cost_details."
      />
      <FindingsSection
        title="Azure spend anomalies"
        subtitle="Material deviations from the trailing baseline"
        path="/api/cost/azure-anomalies"
        params={{ days }}
        emptyMessage="No Azure cost anomaly needs attention."
      />
      <FindingsSection
        title="Azure cost forecast"
        subtitle="P10, P50 and P90 per series — foundry_ai is forecast separately from the total"
        path="/api/cost/azure-forecast"
        emptyMessage="No current Azure forecast is available."
        render={(rows) => <ForecastBySeries rows={rows} />}
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
        subtitle="Month-end outlook per series with explicit currency and cost basis"
        path="/api/cost/forecast"
        emptyMessage="No consolidated forecast is ready."
        render={(rows) => <ForecastBySeries rows={rows} />}
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
        title="Cost"
        description="Understand what was billed, why it changed, and what useful outcome each dollar produced."
      />
      <Tabs tabs={COST_TABS} active={active} onChange={setActive} label="Cost and value views" />
      <div role="tabpanel">
        {active === "databricks" && <Cost />}
        {active === "attribution" && <Attribution />}
        {active === "azure" && <AzureCost />}
        {active === "llm" && <LlmCostView />}
        {active === "budgets" && <Budgets />}
      </div>
    </div>
  );
}

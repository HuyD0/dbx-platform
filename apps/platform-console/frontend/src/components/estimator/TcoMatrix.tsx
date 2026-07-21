import { useId, useState } from "react";
import { compactNum, usd } from "../../lib/format";
import type { EstimateLineItem, EstimateMatrix, EstimateTier } from "../../lib/types";
import { Badge, Card, HelpTip, SectionTitle } from "../ui";
import { adjustedTotals } from "./curve";

const ENVS = ["dev", "uat", "prod"] as const;
const BASELINE_HOURS = 2_000;
const SCENARIOS = ["databricks", "azure"] as const;
const SCENARIO_LABELS: Record<(typeof SCENARIOS)[number], string> = {
  databricks: "Databricks Serverless",
  azure: "Azure Warehouse Compute",
};

type ScenarioKey = (typeof SCENARIOS)[number];

type ScenarioSummary = {
  key: ScenarioKey;
  total: number | null;
  byEnv: Record<string, { total: number; evalTax: number; runCost: number }>;
  computeAllocation: string;
  computeSpend: number | null;
  inputTokensPerSession: number | null;
  outputTokensPerSession: number | null;
  missingPrices: string[];
};

function itemTokens(item: EstimateLineItem): number {
  const unit = item.unit.toLowerCase();
  if (unit.includes("million")) return item.quantity * 1_000_000;
  if (unit.includes("thousand")) return item.quantity * 1_000;
  return item.quantity;
}

function allocationLabel(items: EstimateLineItem[]): string {
  const quantities = new Map<string, number>();
  for (const item of items) {
    quantities.set(item.unit, (quantities.get(item.unit) ?? 0) + item.quantity);
  }
  if (quantities.size === 0) return "No serving allocation";
  return [...quantities.entries()]
    .map(([unit, quantity]) => `${compactNum(quantity)} ${unit}`)
    .join(" + ");
}

function summarizeScenario(
  tier: EstimateTier,
  key: ScenarioKey,
  rigorPct: number,
  monthlySessions: number,
): ScenarioSummary {
  const estimate = tier.scenarios[key];
  if (!estimate) {
    return {
      key,
      total: null,
      byEnv: {},
      computeAllocation: "Scenario unavailable",
      computeSpend: null,
      inputTokensPerSession: null,
      outputTokensPerSession: null,
      missingPrices: [],
    };
  }

  const byEnv = adjustedTotals(estimate, rigorPct);
  const total = ENVS.reduce((sum, env) => sum + (byEnv[env]?.total ?? 0), 0);
  const computeItems = estimate.line_items.filter((item) => item.component === "serving_compute");
  const computeSpend = computeItems.some((item) => item.monthly_cost === null)
    ? null
    : computeItems.reduce((sum, item) => sum + (item.monthly_cost ?? 0), 0);
  const productionModelItems = estimate.line_items.filter(
    (item) => item.component === "model_tokens" && item.env === "prod",
  );
  const tokensPerSession = (direction: "reading" | "writing") => {
    if (!(monthlySessions > 0)) return null;
    const tokens = productionModelItems
      .filter((item) => item.label.toLowerCase().includes(direction))
      .reduce((sum, item) => sum + itemTokens(item), 0);
    return tokens > 0 ? tokens / monthlySessions : null;
  };

  return {
    key,
    total,
    byEnv,
    computeAllocation: allocationLabel(computeItems),
    computeSpend,
    inputTokensPerSession: tokensPerSession("reading"),
    outputTokensPerSession: tokensPerSession("writing"),
    missingPrices: estimate.missing_prices,
  };
}

function summaryValue(value: number | null, format: (value: number) => string): string {
  return value === null ? "Unavailable" : format(value);
}

/** Progressive TCO comparison. The collapsed state keeps the operating-tier
 * decision legible; expansion exposes the exact environment, compute, and
 * inference assumptions without hiding the engine's missing-price signals. */
export function TcoMatrix({
  matrix,
  scenario,
  rigorPct,
  selectedTier,
  onSelectTier,
  baselineHours = BASELINE_HOURS,
}: {
  matrix: EstimateMatrix;
  scenario: string;
  rigorPct: number;
  selectedTier: string;
  onSelectTier: (tier: string) => void;
  baselineHours?: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const detailsId = useId();
  const tierEntries = Object.entries(matrix.tiers);
  const activeEntry = tierEntries.find(([key]) => key === selectedTier) ?? tierEntries[0];

  if (!activeEntry) {
    return (
      <Card>
        <SectionTitle title="Monthly cost comparison" />
        <p className="text-sm text-muted">No operating tiers are available for this estimate.</p>
      </Card>
    );
  }

  const [activeTierKey, activeTier] = activeEntry;
  const monthlySessions = Number(matrix.requirements.monthly_requests ?? 0);
  const summaries = SCENARIOS.map((key) =>
    summarizeScenario(activeTier, key, rigorPct, monthlySessions),
  );
  const requestedScenario = SCENARIOS.includes(scenario as ScenarioKey)
    ? (scenario as ScenarioKey)
    : "databricks";
  const activeSummary = summaries.find((summary) => summary.key === requestedScenario) ?? summaries[0];
  const alternativeSummary = summaries.find((summary) => summary.key !== activeSummary.key);
  const safeBaselineHours = baselineHours > 0 ? baselineHours : BASELINE_HOURS;
  const missingPrices = [...new Set(summaries.flatMap((summary) => summary.missingPrices))];

  const tableRows: {
    label: string;
    detail: string;
    values: [string, string];
  }[] = [
    {
      label: "All-environment monthly spend",
      detail: "DEV, UAT, and production",
      values: summaries.map((summary) => summaryValue(summary.total, usd)) as [string, string],
    },
    {
      label: "Operating run cost",
      detail: "Serving the workload before AI checking coverage",
      values: summaries.map((summary) =>
        summary.total === null
          ? "Unavailable"
          : usd(ENVS.reduce((sum, env) => sum + (summary.byEnv[env]?.runCost ?? 0), 0)),
      ) as [string, string],
    },
    {
      label: "AI checking cost",
      detail: "Evaluation and production-monitoring coverage",
      values: summaries.map((summary) =>
        summary.total === null
          ? "Unavailable"
          : usd(ENVS.reduce((sum, env) => sum + (summary.byEnv[env]?.evalTax ?? 0), 0)),
      ) as [string, string],
    },
    ...ENVS.map((env) => ({
      label:
        env === "dev"
          ? "Development spend"
          : env === "uat"
            ? "Acceptance testing spend"
            : "Production spend",
      detail: "Run cost plus checking coverage",
      values: summaries.map((summary) => usd(summary.byEnv[env]?.total)) as [string, string],
    })),
    {
      label: "Spend per baseline hour",
      detail: `${safeBaselineHours.toLocaleString("en-US")} budgeted hours / month`,
      values: summaries.map((summary) =>
        summaryValue(summary.total, (value) => `${usd(value / safeBaselineHours)} / h`),
      ) as [string, string],
    },
    {
      label: "Serving compute allocation",
      detail: "Monthly capacity assigned across environments",
      values: summaries.map((summary) => summary.computeAllocation) as [string, string],
    },
    {
      label: "Serving compute spend",
      detail: "Compute line items only",
      values: summaries.map((summary) => summaryValue(summary.computeSpend, usd)) as [string, string],
    },
    {
      label: "LLM input tokens / session",
      detail: "Production inference, including context",
      values: summaries.map((summary) =>
        summaryValue(summary.inputTokensPerSession, compactNum),
      ) as [string, string],
    },
    {
      label: "LLM output tokens / session",
      detail: "Production inference output",
      values: summaries.map((summary) =>
        summaryValue(summary.outputTokensPerSession, compactNum),
      ) as [string, string],
    },
  ];

  return (
    <Card>
      <SectionTitle
        title="Monthly cost comparison"
        subtitle={`Top-level spend normalized against an explicit ${safeBaselineHours.toLocaleString(
          "en-US",
        )}-hour monthly baseline.`}
      />

      <div className="mb-3 flex flex-wrap gap-2" role="group" aria-label="Operating tier">
        {tierEntries.map(([tierKey, tier]) => {
          const active = tierKey === activeTierKey;
          return (
            <button
              key={tierKey}
              type="button"
              aria-pressed={active}
              onClick={() => onSelectTier(tierKey)}
              className={`rounded-full border px-3 py-1.5 text-xs font-semibold transition-colors ${
                active
                  ? "border-brand-mid bg-tint text-brand-mid"
                  : "border-grid bg-surface text-ink-2 hover:border-brand-mid/50 hover:text-brand-mid"
              }`}
            >
              {tier.label}
            </button>
          );
        })}
      </div>

      <div
        data-testid="tco-summary-row"
        className="grid gap-4 rounded-xl border border-grid bg-surface p-4 lg:grid-cols-[minmax(0,1.35fr)_repeat(3,minmax(0,1fr))]"
      >
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-brand-mid">
            Selected operating tier
          </p>
          <h3 className="mt-1 text-lg font-semibold text-brand-maroon">{activeTier.label}</h3>
          <p className="mt-1 text-xs leading-5 text-muted">{activeTier.description}</p>
          {activeTier.rigor_locked && (
            <span className="mt-2 inline-block">
              <Badge tone="info">reviews fixed at {activeTier.default_rigor_pct}%</Badge>
            </span>
          )}
        </div>

        <div className="border-t border-grid pt-3 lg:border-l lg:border-t-0 lg:pl-4 lg:pt-0">
          <p className="text-xs text-muted">{SCENARIO_LABELS[activeSummary.key]}</p>
          <p className="mt-1 text-xl font-semibold tabular-nums text-brand-maroon">
            {summaryValue(activeSummary.total, usd)}
          </p>
          <p className="mt-1 text-[11px] text-muted">
            {summaryValue(activeSummary.total, (value) => `${usd(value / safeBaselineHours)} / h`)}
          </p>
        </div>

        <div className="border-t border-grid pt-3 lg:border-l lg:border-t-0 lg:pl-4 lg:pt-0">
          <p className="text-xs text-muted">
            {alternativeSummary ? SCENARIO_LABELS[alternativeSummary.key] : "Alternative scenario"}
          </p>
          <p className="mt-1 text-xl font-semibold tabular-nums text-brand-maroon">
            {alternativeSummary ? summaryValue(alternativeSummary.total, usd) : "Unavailable"}
          </p>
          <p className="mt-1 text-[11px] text-muted">Side-by-side planning alternative</p>
        </div>

        <div className="border-t border-grid pt-3 lg:border-l lg:border-t-0 lg:pl-4 lg:pt-0">
          <p className="text-xs text-muted">Baseline capacity budget</p>
          <p className="mt-1 text-xl font-semibold tabular-nums text-brand-maroon">
            {safeBaselineHours.toLocaleString("en-US")} h
          </p>
          <p className="mt-1 text-[11px] text-muted">Per month · explicit comparison basis</p>
        </div>
      </div>

      {missingPrices.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2" role="status">
          <Badge tone="warning">{missingPrices.length} price(s) unavailable</Badge>
          <HelpTip label="About missing prices">
            Some meters had no price in the current snapshot, so these totals are incomplete —
            never silently zero. Refresh the pricing snapshot to fill the gaps.
          </HelpTip>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted">
          Expand for environment allocation, compute capacity, and inference assumptions.
        </p>
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={detailsId}
          onClick={() => setExpanded((open) => !open)}
          className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-brand-mid px-3 py-1.5 text-xs font-semibold text-brand-mid transition-colors hover:bg-tint"
        >
          <span aria-hidden="true" className="text-sm">
            {expanded ? "−" : "+"}
          </span>
          {expanded ? "Hide detailed comparison" : "Expand detailed comparison"}
        </button>
      </div>

      {expanded && (
        <div id={detailsId} className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm" data-testid="tco-matrix">
            <caption className="sr-only">
              Detailed cost, compute allocation, and LLM inference comparison
            </caption>
            <thead className="bg-warning-surface text-left text-xs text-brand-maroon">
              <tr>
                <th scope="col" className="rounded-l-lg border-y border-l border-grid px-3 py-3 font-semibold">
                  Cost and allocation detail
                </th>
                {SCENARIOS.map((key, index) => (
                  <th
                    key={key}
                    scope="col"
                    className={`border-y border-grid px-3 py-3 font-semibold ${
                      index === SCENARIOS.length - 1 ? "rounded-r-lg border-r" : ""
                    }`}
                  >
                    {SCENARIO_LABELS[key]}
                    {key === requestedScenario && (
                      <span className="ml-2 rounded-full bg-tint px-1.5 py-0.5 text-[10px] text-brand-mid">
                        active
                      </span>
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-grid">
              {tableRows.map((row) => (
                <tr key={row.label} className="align-top hover:bg-tint/50">
                  <th scope="row" className="px-3 py-3 text-left">
                    <span className="block text-xs font-semibold text-ink">{row.label}</span>
                    <span className="mt-0.5 block text-[11px] font-normal text-muted">
                      {row.detail}
                    </span>
                  </th>
                  {row.values.map((value, index) => (
                    <td key={SCENARIOS[index]} className="px-3 py-3 tabular-nums text-ink-2">
                      {value}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>

        </div>
      )}
    </Card>
  );
}

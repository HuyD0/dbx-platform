import { ChevronDown, ChevronRight } from "lucide-react";
import { Fragment, useState } from "react";
import type { EstimateMatrix, TierScenarioEstimate } from "../../lib/types";
import { usd } from "../../lib/format";
import { Badge, Card, HelpTip, SectionTitle } from "../ui";
import { adjustedTotals } from "./curve";

const ENVS = ["dev", "uat", "prod"] as const;
const ENV_LABELS: Record<string, string> = {
  dev: "Development",
  uat: "Acceptance testing",
  prod: "Production",
};

const SCENARIO_LABELS: Record<string, string> = {
  databricks: "Databricks",
  snowflake: "Snowflake",
  azure: "Azure",
};

function scenarioLabel(key: string): string {
  return SCENARIO_LABELS[key] ?? key.charAt(0).toUpperCase() + key.slice(1);
}

/** All-environment grand total (and checking share) for one scenario at the
 * current review-coverage. Used both for the headline cells and the expanded
 * cross-scenario comparison — the math stays identical to the matrix cells. */
function grandFor(estimate: TierScenarioEstimate, rigorPct: number) {
  const totals = adjustedTotals(estimate, rigorPct);
  let total = 0;
  let evalTax = 0;
  for (const env of ENVS) {
    total += totals[env]?.total ?? 0;
    evalTax += totals[env]?.evalTax ?? 0;
  }
  return { total, evalTax, runCost: total - evalTax };
}

/** Checking (eval-tax) share above this fraction of the total is flagged as a
 * cost anomaly in the expanded view — the AI is spending more on verifying
 * itself than a typical tier would. */
const CHECKING_ANOMALY_SHARE = 0.5;

/** The 3-tier × DEV/UAT/PROD dollar matrix. Each cell separates the cost of
 * running the AI from the cost of checking it; totals react instantly to the
 * review-coverage slider via the server-provided coefficients.
 *
 * Progressive disclosure: the matrix defaults to this high-level split, and
 * each tier row expands to reveal the per-environment breakdown and the
 * Databricks/Snowflake/Azure cost differential without leaving the table. */
export function TcoMatrix({
  matrix,
  scenario,
  rigorPct,
  selectedTier,
  onSelectTier,
}: {
  matrix: EstimateMatrix;
  scenario: string;
  rigorPct: number;
  selectedTier: string;
  onSelectTier: (tier: string) => void;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <Card>
      <SectionTitle
        title="Monthly cost by tier and environment"
        subtitle="Each cell shows total / month, split into running the AI and checking its work. Expand a tier for the per-environment and cross-platform breakdown."
      />
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm" data-testid="tco-matrix">
          <caption className="sr-only">
            Monthly total cost of ownership by tier and environment
          </caption>
          <thead>
            <tr className="text-left text-xs text-muted">
              <th scope="col" className="py-2 pr-3 font-medium">
                Tier
              </th>
              {ENVS.map((env) => (
                <th key={env} scope="col" className="px-3 py-2 font-medium">
                  {ENV_LABELS[env]}
                </th>
              ))}
              <th scope="col" className="px-3 py-2 font-medium">
                All environments
              </th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(matrix.tiers).map(([tierKey, tier]) => {
              const estimate = tier.scenarios[scenario];
              if (!estimate) return null;
              const totals = adjustedTotals(estimate, rigorPct);
              const grand = ENVS.reduce((sum, env) => sum + (totals[env]?.total ?? 0), 0);
              const active = tierKey === selectedTier;
              const isOpen = expanded === tierKey;
              const detailId = `tco-detail-${tierKey}`;

              // Cross-scenario comparison for this tier, cheapest first.
              const current = grandFor(estimate, rigorPct);
              const comparisons = Object.entries(tier.scenarios)
                .map(([scKey, scEstimate]) => ({
                  scKey,
                  ...grandFor(scEstimate, rigorPct),
                }))
                .sort((a, b) => a.total - b.total);
              const checkingShare = current.total > 0 ? current.evalTax / current.total : 0;
              const checkingAnomaly = checkingShare >= CHECKING_ANOMALY_SHARE;

              return (
                <Fragment key={tierKey}>
                  <tr
                    className={`border-t border-hairline align-top ${
                      active ? "bg-series-1/5" : ""
                    }`}
                  >
                    <th scope="row" className="max-w-52 py-3 pr-3 text-left">
                      <div className="flex items-start gap-1.5">
                        <button
                          type="button"
                          aria-expanded={isOpen}
                          aria-controls={detailId}
                          onClick={() => setExpanded(isOpen ? null : tierKey)}
                          className="mt-0.5 rounded p-0.5 text-muted hover:bg-hairline hover:text-ink"
                          title={isOpen ? "Hide breakdown" : "Show breakdown"}
                        >
                          {isOpen ? (
                            <ChevronDown className="h-3.5 w-3.5" />
                          ) : (
                            <ChevronRight className="h-3.5 w-3.5" />
                          )}
                          <span className="sr-only">
                            {isOpen ? "Hide" : "Show"} {tier.label} breakdown
                          </span>
                        </button>
                        <button
                          type="button"
                          aria-pressed={active}
                          onClick={() => onSelectTier(tierKey)}
                          className="min-w-0 text-left"
                        >
                          <span className="block text-sm font-semibold text-ink">
                            {tier.label}
                          </span>
                          <span className="mt-0.5 block text-xs font-normal text-muted">
                            {tier.description}
                          </span>
                          {tier.rigor_locked && (
                            <span className="mt-1 inline-block">
                              <Badge tone="info">reviews fixed at {estimate.rigor_pct}%</Badge>
                            </span>
                          )}
                        </button>
                      </div>
                    </th>
                    {ENVS.map((env) => {
                      const cell = totals[env];
                      return (
                        <td key={env} className="px-3 py-3 tabular-nums">
                          <span className="block font-semibold text-ink">
                            {usd(cell?.total)}
                          </span>
                          <span className="block text-xs text-muted">
                            run {usd(cell?.runCost)}
                          </span>
                          <span className="block text-xs text-series-2">
                            checking {usd(cell?.evalTax)}
                          </span>
                        </td>
                      );
                    })}
                    <td className="px-3 py-3 font-semibold tabular-nums text-ink">
                      {usd(grand)}
                      {estimate.missing_prices.length > 0 && (
                        <span className="mt-1 block">
                          <Badge tone="warning">
                            {estimate.missing_prices.length} price(s) unavailable
                          </Badge>
                          <HelpTip label="About missing prices">
                            Some meters had no price in the current snapshot, so this
                            total is incomplete — never silently zero. Refresh the
                            pricing snapshot to fill the gaps.
                          </HelpTip>
                        </span>
                      )}
                    </td>
                  </tr>
                  {isOpen && (
                    <tr className="border-t border-hairline bg-page/40" data-testid={detailId}>
                      <td colSpan={ENVS.length + 2} className="px-3 py-4">
                        <div className="grid gap-4 lg:grid-cols-2">
                          <div>
                            <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">
                              Per-environment split
                            </h4>
                            <dl className="mt-2 space-y-1.5">
                              {ENVS.map((env) => {
                                const cell = totals[env];
                                return (
                                  <div
                                    key={env}
                                    className="flex items-baseline justify-between gap-3 border-b border-hairline pb-1.5 text-xs last:border-0"
                                  >
                                    <dt className="text-ink-2">{ENV_LABELS[env]}</dt>
                                    <dd className="tabular-nums text-muted">
                                      run{" "}
                                      <span className="font-medium text-ink">
                                        {usd(cell?.runCost)}
                                      </span>{" "}
                                      · checking{" "}
                                      <span className="font-medium text-series-2">
                                        {usd(cell?.evalTax)}
                                      </span>
                                    </dd>
                                  </div>
                                );
                              })}
                            </dl>
                            {checkingAnomaly && (
                              <p className="mt-2">
                                <Badge tone="warning">
                                  checking is {Math.round(checkingShare * 100)}% of total
                                </Badge>
                                <HelpTip label="Why is this flagged?">
                                  Verifying the AI's work costs more than half of this
                                  tier's total spend — unusually high. Lower the review
                                  coverage or revisit the checking strategy if this is
                                  unexpected.
                                </HelpTip>
                              </p>
                            )}
                          </div>
                          <div>
                            <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">
                              Platform differential (all environments)
                            </h4>
                            <dl className="mt-2 space-y-1.5">
                              {comparisons.map((row) => {
                                const delta = row.total - current.total;
                                const isCurrent = row.scKey === scenario;
                                return (
                                  <div
                                    key={row.scKey}
                                    className="flex items-baseline justify-between gap-3 border-b border-hairline pb-1.5 text-xs last:border-0"
                                  >
                                    <dt className="flex items-center gap-1.5 text-ink-2">
                                      {scenarioLabel(row.scKey)}
                                      {isCurrent && <Badge tone="info">selected</Badge>}
                                    </dt>
                                    <dd className="tabular-nums">
                                      <span className="font-medium text-ink">
                                        {usd(row.total)}
                                      </span>
                                      {!isCurrent && delta !== 0 && (
                                        <span className="ml-1.5 text-muted">
                                          {delta > 0 ? "+" : "−"}
                                          {usd(Math.abs(delta))}
                                        </span>
                                      )}
                                    </dd>
                                  </div>
                                );
                              })}
                            </dl>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

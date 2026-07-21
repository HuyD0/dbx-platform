import { useId, useState } from "react";
import type { EstimateMatrix } from "../../lib/types";
import { usd } from "../../lib/format";
import { Badge, Card, HelpTip, SectionTitle } from "../ui";
import { allEnvTotal } from "./curve";

const STORAGE_KEY = "estimator.budget";
const DEFAULT_BUDGET = 5_000;

function loadBudget(): number {
  try {
    const stored = Number(window.localStorage.getItem(STORAGE_KEY));
    return Number.isFinite(stored) && stored > 0 ? stored : DEFAULT_BUDGET;
  } catch {
    return DEFAULT_BUDGET;
  }
}

function saveBudget(value: number) {
  try {
    window.localStorage.setItem(STORAGE_KEY, String(value));
  } catch {
    // A private-mode / disabled storage must never break the estimate.
  }
}

/** A monthly-spend budget line drawn over the engine's real all-environment
 * total — the honest analog of the reference tool's hour-cap bar. It never
 * fabricates a rate: the bar only compares the computed total against a target
 * the user sets, and names the cheapest tier that would fit if it is over. */
export function BudgetBar({
  matrix,
  scenario,
  rigorPct,
  selectedTier,
}: {
  matrix: EstimateMatrix;
  scenario: string;
  rigorPct: number;
  selectedTier: string;
}) {
  const inputId = useId();
  const [budget, setBudget] = useState(loadBudget);

  const tierEntries = Object.entries(matrix.tiers);
  const activeEntry =
    tierEntries.find(([key]) => key === selectedTier) ?? tierEntries[0];
  if (!activeEntry) return null;
  const [, activeTier] = activeEntry;
  const activeEstimate = activeTier.scenarios[scenario];
  if (!activeEstimate) return null;

  const incomplete = activeEstimate.missing_prices.length > 0;
  const total = allEnvTotal(activeEstimate, rigorPct);
  const over = total > budget;
  const overBy = Math.max(0, total - budget);

  // Cheapest tier whose all-environment total fits the budget, for the "what
  // would fit" hint. Only meaningful when the current selection is over.
  const fitting = tierEntries
    .map(([key, tier]) => {
      const estimate = tier.scenarios[scenario];
      return estimate
        ? { key, label: tier.label, total: allEnvTotal(estimate, rigorPct) }
        : null;
    })
    .filter((row): row is { key: string; label: string; total: number } => row !== null)
    .filter((row) => row.total <= budget)
    .sort((a, b) => a.total - b.total);
  const cheaperFit = fitting.find((row) => row.key !== activeEntry[0]) ?? fitting[0];

  const scale = Math.max(budget, total, 1) * 1.12;
  const withinPct = (Math.min(total, budget) / scale) * 100;
  const overPct = (overBy / scale) * 100;
  const budgetPct = (budget / scale) * 100;

  const updateBudget = (value: number) => {
    const next = Number.isFinite(value) && value > 0 ? Math.round(value) : 0;
    setBudget(next);
    if (next > 0) saveBudget(next);
  };

  return (
    <Card>
      <SectionTitle
        title="Monthly budget"
        subtitle="Where this estimate lands against a spend target you set."
        right={
          <HelpTip label="How the budget bar works">
            The bar compares your target against the engine's all-environment
            monthly total (DEV + UAT + production). It is a planning ceiling over
            a real, priced number — it does not change any rate or apply a
            discount.
          </HelpTip>
        }
      />

      <div className="flex flex-wrap items-end gap-4">
        <label htmlFor={inputId} className="text-sm">
          <span className="font-medium text-ink">Target / month (USD)</span>
          <div className="mt-1.5 flex items-center gap-1.5">
            <span className="text-sm text-muted">$</span>
            <input
              id={inputId}
              type="number"
              min={0}
              step={100}
              value={budget || ""}
              onChange={(event) => updateBudget(Number(event.target.value))}
              className="w-36 rounded-lg border border-hairline bg-page px-3 py-2 text-sm tabular-nums text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-series-1"
            />
          </div>
        </label>
        <div className="text-sm tabular-nums">
          <span className="block font-semibold text-ink">{usd(total)} / month</span>
          <span className="block text-xs text-muted">
            all-environment total · {activeTier.label}
          </span>
        </div>
      </div>

      {incomplete ? (
        <p className="mt-4 text-xs text-muted">
          Some meters are unpriced in the current snapshot, so this total is
          incomplete — the bar would understate spend. Refresh the pricing
          snapshot for a budget comparison.
        </p>
      ) : (
        <>
          <div
            className="relative mt-4 h-8 overflow-hidden rounded-lg border border-grid bg-page"
            role="img"
            aria-label={`${usd(total)} of a ${usd(budget)} monthly budget`}
          >
            <div
              className="absolute inset-y-0 left-0 bg-series-1 transition-[width] duration-300"
              style={{ width: `${withinPct}%` }}
            />
            <div
              className="absolute inset-y-0 bg-warning-accent transition-[width,left] duration-300"
              style={{ left: `${withinPct}%`, width: `${overPct}%` }}
            />
            <div
              className="absolute inset-y-0 w-0.5 bg-brand-maroon transition-[left] duration-300"
              style={{ left: `${budgetPct}%` }}
              aria-hidden="true"
            />
          </div>
          <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
            <span className="inline-flex items-center gap-1.5">
              <span className="inline-block h-2.5 w-2.5 rounded-sm bg-series-1" />
              Within budget
            </span>
            <span className="tabular-nums">Target {usd(budget)}</span>
          </div>

          {over && (
            <div className="mt-3 flex flex-wrap items-center gap-2" role="status">
              <Badge tone="warning">{usd(overBy)} over budget</Badge>
              <p className="text-xs text-muted">
                {cheaperFit
                  ? `The ${cheaperFit.label} tier fits at ${usd(cheaperFit.total)} / month.`
                  : "No operating tier fits this budget at the current sizing — lower the target or the workload."}
              </p>
            </div>
          )}
        </>
      )}
    </Card>
  );
}

import type { EstimateMatrix } from "../../lib/types";
import { usd } from "../../lib/format";
import { Badge, Card, HelpTip, SectionTitle } from "../ui";
import { adjustedTotals } from "./curve";

const ENVS = ["dev", "uat", "prod"] as const;
const ENV_LABELS: Record<string, string> = {
  dev: "Development",
  uat: "Acceptance testing",
  prod: "Production",
};

/** The 3-tier × DEV/UAT/PROD dollar matrix. Each cell separates the cost of
 * running the AI from the cost of checking it; totals react instantly to the
 * review-coverage slider via the server-provided coefficients. */
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
  return (
    <Card>
      <SectionTitle
        title="Monthly cost by tier and environment"
        subtitle="Each cell shows total / month, split into running the AI and checking its work."
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
              return (
                <tr
                  key={tierKey}
                  className={`border-t border-hairline align-top ${
                    active ? "bg-series-1/5" : ""
                  }`}
                >
                  <th scope="row" className="max-w-52 py-3 pr-3 text-left">
                    <button
                      type="button"
                      aria-pressed={active}
                      onClick={() => onSelectTier(tierKey)}
                      className="text-left"
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
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

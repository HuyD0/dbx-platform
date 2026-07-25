import { useId } from "react";
import type { EstimateTier } from "../../lib/types";
import { usd } from "../../lib/format";
import { Card, SectionTitle } from "../ui";
import { applyRigor } from "./curve";

/** The Evaluation Rigor slider: what share of live production answers gets an
 * AI-graded review. Affine coefficients from the engine make the recompute
 * instant and exact; locked tiers render the slider pinned with a reason. */
export function RigorSlider({
  tier,
  scenario,
  rigorPct,
  onChange,
}: {
  tier: EstimateTier;
  scenario: string;
  rigorPct: number;
  onChange: (value: number) => void;
}) {
  const sliderId = useId();
  const estimate = tier.scenarios[scenario];
  if (!estimate) return null;
  const locked = tier.rigor_locked;
  const effective = locked ? estimate.rigor_pct : rigorPct;
  const prodCurve = estimate.rigor_curve.by_env.prod;
  const { evalTax } = applyRigor(prodCurve, effective);
  const perPoint = prodCurve.eval_slope_per_pct;

  return (
    <Card>
      <SectionTitle
        title="How many live answers get an AI-graded review?"
        subtitle="Code-based checks always run on everything at near-zero cost; this controls the paid reviews."
      />
      <div className="flex flex-wrap items-center gap-4">
        <div className="min-w-56 flex-1">
          <label htmlFor={sliderId} className="text-xs font-medium text-ink">
            Review coverage: <span className="tabular-nums">{effective}%</span> of
            production answers
          </label>
          <input
            id={sliderId}
            type="range"
            min={0}
            max={100}
            step={1}
            value={effective}
            disabled={locked}
            onChange={(event) => onChange(Number(event.target.value))}
            aria-valuetext={`${effective} percent of production answers reviewed`}
            className="mt-2 w-full accent-series-1 disabled:opacity-50"
          />
        </div>
        <div className="text-sm tabular-nums">
          <span className="block font-semibold text-ink">{usd(evalTax)} / month</span>
          <span className="block text-xs text-muted">
            checking cost in production
            {!locked && perPoint > 0 && ` · ${usd(perPoint)} per +1%`}
          </span>
        </div>
      </div>
      {locked && (
        <p className="mt-2 text-xs text-muted" data-testid="rigor-locked-reason">
          {tier.rigor_locked_reason}
        </p>
      )}
    </Card>
  );
}

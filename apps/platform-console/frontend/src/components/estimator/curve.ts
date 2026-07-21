import type { RigorCurveEnv, TierScenarioEstimate } from "../../lib/types";

/** Exact client-side recompute of totals for a review-coverage value.
 *
 * The engine's judge-token and activity-record formulas are affine in the
 * rigor percentage, so the server ships fixed + slope coefficients per
 * environment and the slider never needs a round-trip: these numbers match
 * the engine to the cent.
 */
export function applyRigor(curve: RigorCurveEnv, rigorPct: number) {
  const pct = Math.max(0, Math.min(100, rigorPct));
  return {
    total: curve.total_fixed + curve.total_slope_per_pct * pct,
    evalTax: curve.eval_fixed + curve.eval_slope_per_pct * pct,
  };
}

export function effectiveRigor(estimate: TierScenarioEstimate, requested: number): number {
  return estimate.rigor_curve.pinned ? estimate.rigor_pct : requested;
}

export function adjustedTotals(estimate: TierScenarioEstimate, requested: number) {
  const rigor = effectiveRigor(estimate, requested);
  const byEnv: Record<string, { total: number; evalTax: number; runCost: number }> = {};
  for (const [env, curve] of Object.entries(estimate.rigor_curve.by_env)) {
    const { total, evalTax } = applyRigor(curve, rigor);
    byEnv[env] = { total, evalTax, runCost: total - evalTax };
  }
  return byEnv;
}

/** All-environment monthly spend — the same DEV+UAT+PROD headline number the
 * TCO matrix shows, exposed as one call so the budget bar and unit economics
 * cannot drift from it. */
export function allEnvTotal(estimate: TierScenarioEstimate, requested: number): number {
  const byEnv = adjustedTotals(estimate, requested);
  return Object.values(byEnv).reduce((sum, env) => sum + env.total, 0);
}

/** Production-only monthly spend — the denominator for per-session economics
 * (dev/UAT are fixed testing overhead, not scaled by production traffic). */
export function prodTotal(estimate: TierScenarioEstimate, requested: number): number {
  return adjustedTotals(estimate, requested).prod?.total ?? 0;
}

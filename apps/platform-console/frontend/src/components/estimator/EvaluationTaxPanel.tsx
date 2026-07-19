import type { TierScenarioEstimate } from "../../lib/types";
import { BarList } from "../BarList";
import { Card, SectionTitle } from "../ui";
import { adjustedTotals } from "./curve";

/** Visually isolates the "evaluation tax": what checking the AI's work costs
 * versus running it, with the improvement pipeline (making it better in
 * dev/test) broken out from production monitoring. */
export function EvaluationTaxPanel({
  estimate,
  rigorPct,
}: {
  estimate: TierScenarioEstimate;
  rigorPct: number;
}) {
  const totals = adjustedTotals(estimate, rigorPct);
  const run = Object.values(totals).reduce((sum, env) => sum + env.runCost, 0);
  const evalTax = Object.values(totals).reduce((sum, env) => sum + env.evalTax, 0);
  const improvement = Object.values(estimate.improvement_pipeline_by_env).reduce(
    (sum, value) => sum + value,
    0,
  );
  const monitoring = Math.max(evalTax - improvement, 0);

  return (
    <Card>
      <SectionTitle
        title="Cost of checking the AI's work"
        subtitle="Kept separate on purpose: trustworthy AI has a visible price, and this is it."
      />
      <BarList
        data={[
          { label: "Running the AI (all environments)", value: run },
          { label: "Watching it in production (reviews + records)", value: monitoring },
          { label: "Making it better (test suites before release)", value: improvement },
        ]}
      />
    </Card>
  );
}

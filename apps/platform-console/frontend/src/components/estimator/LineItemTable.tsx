import type { TierScenarioEstimate } from "../../lib/types";
import { DataTable } from "../DataTable";
import { Card, SectionTitle } from "../ui";

/** Full formula transparency: every line is quantity × unit price = cost with
 * its meter, snapshot date and named assumptions. CSV export via DataTable. */
export function LineItemTable({ estimate }: { estimate: TierScenarioEstimate }) {
  const rows = estimate.line_items.map((item) => ({
    what: item.label,
    environment: item.env,
    "cost / month": item.monthly_cost ?? "price unavailable",
    "how it was computed": item.formula,
    meter: item.meter_name ?? "—",
    "price source": item.price_source ?? "—",
    "priced on": item.snapshot_date ?? "—",
    assumptions: item.assumptions.join(", "),
    "checking cost": item.is_eval_tax ? "yes" : "no",
    _provenance: item.provenance,
  }));
  return (
    <Card>
      <SectionTitle
        title="Every line item, shown in full"
        subtitle="Nothing is a black box: challenge any quantity, price or assumption."
      />
      <DataTable
        rows={rows}
        caption="Cost line items"
        exportName={`ai-cost-planner-${estimate.tier}-${estimate.scenario}`}
        pageSize={12}
      />
    </Card>
  );
}

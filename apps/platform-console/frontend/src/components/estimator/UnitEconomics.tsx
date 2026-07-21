import type { TierScenarioEstimate } from "../../lib/types";
import { usd } from "../../lib/format";
import { Badge, Card, HelpTip, SectionTitle, StatTile } from "../ui";
import { allEnvTotal, prodTotal } from "./curve";

/** Per-session unit economics, derived straight from the engine's own totals —
 * no new server math, no fabricated rates. Per-session figures use the
 * production total over production traffic (dev/UAT are fixed testing overhead
 * that does not scale with each request); the annual run-rate uses the full
 * all-environment spend. When a meter is missing the estimate is incomplete, so
 * we surface that instead of dividing an understated total into a confident,
 * too-cheap unit cost. */
export function UnitEconomics({
  estimate,
  rigorPct,
  monthlySessions,
}: {
  estimate: TierScenarioEstimate;
  rigorPct: number;
  monthlySessions: number;
}) {
  const incomplete = estimate.missing_prices.length > 0;
  const prod = prodTotal(estimate, rigorPct);
  const annual = allEnvTotal(estimate, rigorPct) * 12;
  const hasSessions = monthlySessions > 0;

  const perSession = hasSessions ? usd(prod / monthlySessions) : "—";
  const per1k = hasSessions ? usd((prod / monthlySessions) * 1000) : "—";

  return (
    <Card>
      <SectionTitle
        title="Unit economics"
        subtitle="What each production session costs, and the annual run-rate."
        right={
          <HelpTip label="How unit economics are derived">
            Per-session cost is the production monthly total divided by
            production sessions. Development and acceptance-testing spend is
            fixed overhead that does not scale per session, so it is excluded
            from the per-session figures but included in the annual run-rate.
          </HelpTip>
        }
      />
      {incomplete ? (
        <div className="flex flex-wrap items-center gap-2" role="status">
          <Badge tone="warning">
            {estimate.missing_prices.length} price(s) unavailable
          </Badge>
          <p className="text-xs text-muted">
            Some meters had no price in the current snapshot, so per-session
            costs would be understated — never silently zero. Refresh the
            pricing snapshot to compute unit economics.
          </p>
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-3">
          <StatTile
            label="Cost / session"
            value={perSession}
            hint="Production spend per session"
          />
          <StatTile
            label="Cost / 1K sessions"
            value={per1k}
            hint="Production spend per 1,000 sessions"
          />
          <StatTile
            label="Annual run-rate"
            value={usd(annual)}
            hint="All-environment monthly spend × 12"
          />
        </div>
      )}
    </Card>
  );
}

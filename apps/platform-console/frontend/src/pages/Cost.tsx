import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { FindingsSection } from "../components/FindingsSection";
import {
  ProductSpendBreakdown,
  type FoundrySourceState,
} from "../components/ProductSpendBreakdown";
import { Card, SectionTitle } from "../components/ui";
import { apiGet } from "../lib/api";
import type { Envelope, Row } from "../lib/types";

const WINDOWS = [7, 30, 90];

export function Cost() {
  const [days, setDays] = useState(30);
  const foundry = useQuery({
    queryKey: ["/api/cost/foundry-attribution", { days }],
    queryFn: () =>
      apiGet<Envelope<Row[]>>("/api/cost/foundry-attribution", { days }),
    staleTime: 60_000,
    retry: false,
  });
  const sourceStatus = foundry.data?.source_status;
  const foundrySource: FoundrySourceState = foundry.isPending
    ? { status: "loading", rows: [] }
    : foundry.isError
      ? {
          status: "error",
          rows: [],
          message: "Foundry actuals could not be loaded. Check Azure cost source health and retry.",
        }
      : sourceStatus && !["healthy", "available"].includes(sourceStatus.status.toLowerCase())
        ? {
            status: "unavailable",
            rows: [],
            message: sourceStatus.notes,
          }
        : {
            status: "ready",
            rows: foundry.data?.data ?? [],
            message: sourceStatus?.notes,
          };
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-1 text-xs">
        <span className="mr-1 text-muted">Window:</span>
        {WINDOWS.map((w) => (
          <button
            key={w}
            type="button"
            onClick={() => setDays(w)}
            aria-pressed={days === w}
            className={`rounded-lg px-2.5 py-1 font-medium ${
              days === w ? "bg-accent text-white" : "border border-grid text-ink-2 hover:bg-hairline"
            }`}
          >
            {w}d
          </button>
        ))}
      </div>

      <Card>
        <SectionTitle
          title="Cost command center"
          subtitle="One high-level view before drilling into billable line items."
        />
        <div className="grid gap-3 md:grid-cols-3">
          <div className="rounded-xl border border-grid bg-page p-3">
            <p className="text-xs font-semibold text-ink">Total Databricks cost</p>
            <p className="mt-1 text-xs leading-5 text-muted">
              The workspace total is shown in the product breakdown below and reconciles Databricks
              usage at list price by product, workload, SKU, and usage unit.
            </p>
          </div>
          <div className="rounded-xl border border-grid bg-page p-3">
            <p className="text-xs font-semibold text-ink">Azure bill reconciliation</p>
            <p className="mt-1 text-xs leading-5 text-muted">
              Azure Cost Management is the bill-of-record for invoiced totals. Databricks line
              items can be tied back by workspace, SKU, meter, tags, and date, but Azure may still
              roll up credits, taxes, marketplace, and rounding outside item-level attribution.
            </p>
          </div>
          <div className="rounded-xl border border-grid bg-page p-3">
            <p className="text-xs font-semibold text-ink">Data quality controls</p>
            <p className="mt-1 text-xs leading-5 text-muted">
              Scheduled evidence jobs refresh source health, normalize findings, flag unpriced or
              untagged usage, and keep approval-only remediation separate from read-only reporting.
            </p>
          </div>
        </div>
      </Card>

      <FindingsSection
        title="Workspace spend by product"
        subtitle={`Total cost, product groups, and workload drill-down for the last ${days} days`}
        path="/api/cost/products"
        params={{ days }}
        emptyMessage="No billed usage in the window."
        renderWhenEmpty
        render={(rows) => (
          <ProductSpendBreakdown rows={rows} days={days} foundrySource={foundrySource} />
        )}
      />
      <FindingsSection
        title="Most expensive jobs"
        subtitle={
          "Job names are shown when usage includes job metadata; missing descriptions require " +
          "upstream job tags or an enrichment source."
        }
        path="/api/cost/top-jobs"
        params={{ days }}
        emptyMessage="No job spend in the window."
      />
      <FindingsSection
        title="Under-utilized clusters"
        subtitle="Observed load does not justify the size — ranked by cost"
        path="/api/cost/cluster-utilization"
        params={{ days }}
        emptyMessage="No under-utilized clusters."
      />
      <FindingsSection
        title="Mis-sized SQL warehouses"
        subtitle="Idle spend or sustained queueing at capacity"
        path="/api/cost/warehouse-utilization"
        params={{ days }}
        emptyMessage="No mis-sized warehouses."
      />
      <FindingsSection
        title="Failed-run waste"
        subtitle="List cost burned on failed or timed-out runs"
        path="/api/cost/failed-run-waste"
        params={{ days }}
        emptyMessage="No failed-run waste."
      />
    </div>
  );
}

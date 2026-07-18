import { useState } from "react";
import { BarList } from "../components/BarList";
import { FindingsSection } from "../components/FindingsSection";

const WINDOWS = [7, 30, 90];

export function Cost() {
  const [days, setDays] = useState(30);
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

      <FindingsSection
        title="Spend by SKU and workspace"
        subtitle={`DBUs and list cost, last ${days} days`}
        path="/api/cost/usage"
        params={{ days }}
        emptyMessage="No billed usage in the window."
        render={(rows) => (
          <BarList
            maxBars={12}
            data={rows.map((r) => ({
              label: String(r.sku_name ?? "unknown"),
              value: Number(r.list_cost_usd ?? 0),
            }))}
          />
        )}
      />
      <FindingsSection
        title="Most expensive jobs"
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

import { useState } from "react";
import { BarList } from "../components/BarList";
import { FindingsSection } from "../components/FindingsSection";

const WINDOWS = [7, 30, 90];
const DIMENSIONS = [
  { value: "workload_type", label: "Product" },
  { value: "sku_name", label: "SKU" },
  { value: "project", label: "Project" },
  { value: "app", label: "App" },
] as const;

export function Cost() {
  const [days, setDays] = useState(30);
  const [dimension, setDimension] = useState<(typeof DIMENSIONS)[number]["value"]>(
    "workload_type",
  );
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
        title="Databricks spend attribution"
        subtitle={`Workspace-scoped DBUs and list cost, last ${days} days`}
        path="/api/cost/usage"
        params={{ days }}
        emptyMessage="No billed usage in the window."
        render={(rows) => (
          <div className="space-y-3">
            <label className="flex items-center gap-2 text-xs text-muted">
              Dimension
              <select
                value={dimension}
                onChange={(event) =>
                  setDimension(
                    event.target.value as (typeof DIMENSIONS)[number]["value"],
                  )
                }
                className="rounded-lg border border-grid bg-page px-2 py-1 text-ink"
              >
                {DIMENSIONS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            <BarList
              maxBars={12}
              data={Array.from(
                rows.reduce((grouped, row) => {
                  const label = String(row[dimension] ?? "unallocated");
                  grouped.set(
                    label,
                    (grouped.get(label) ?? 0) + Number(row.list_cost_usd ?? 0),
                  );
                  return grouped;
                }, new Map<string, number>()),
                ([label, value]) => ({ label, value }),
              ).sort((left, right) => right.value - left.value)}
            />
          </div>
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

import { useMemo, useState } from "react";
import { num, usd } from "../lib/format";
import type { Row } from "../lib/types";

type ProductTotal = {
  key: string;
  label: string;
  current: number;
  previous: number;
};

/** Categorical fills for the composition bar and the matching ranked list. These
 * are the non-red chart steps (teal / green / gold); series-3 is skipped because
 * it resolves to the same red reserved for the product brand and primary actions. */
const SERIES_FILLS = ["bg-series-1", "bg-series-2", "bg-series-4"];

const PRODUCT_LABELS: Record<string, string> = {
  APPS: "Databricks Apps",
  DATABASE: "Lakebase",
  LAKEBASE: "Lakebase",
  JOBS: "Jobs",
  DLT: "Lakeflow pipelines",
  SQL: "SQL warehouses",
  ALL_PURPOSE: "All-purpose compute",
  INTERACTIVE: "Serverless interactive",
  MODEL_SERVING: "Model serving",
  VECTOR_SEARCH: "AI Search",
  AI_GATEWAY: "AI Gateway",
  AI_RUNTIME: "AI Runtime",
  AI_FUNCTIONS: "AI Functions",
  FOUNDATION_MODEL_TRAINING: "Model training",
  PREDICTIVE_OPTIMIZATION: "Predictive optimization",
  NETWORKING: "Networking",
  DEFAULT_STORAGE: "Default storage",
  UNATTRIBUTED: "Unattributed",
};

function productKey(value: unknown): string {
  const key = String(value ?? "UNATTRIBUTED").toUpperCase();
  return key === "DATABASE" ? "LAKEBASE" : key;
}

export function productLabel(value: unknown): string {
  const key = productKey(value);
  return (
    PRODUCT_LABELS[key] ??
    key
      .toLowerCase()
      .replace(/_/g, " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase())
  );
}

export function aggregateProductSpend(rows: Row[]): ProductTotal[] {
  const totals = new Map<string, ProductTotal>();
  for (const row of rows) {
    const key = productKey(row.product);
    const existing = totals.get(key) ?? {
      key,
      label: productLabel(key),
      current: 0,
      previous: 0,
    };
    const cost = Number(row.list_cost_usd ?? 0);
    if (row.period === "previous") existing.previous += Number.isFinite(cost) ? cost : 0;
    if (row.period === "current") existing.current += Number.isFinite(cost) ? cost : 0;
    totals.set(key, existing);
  }
  return [...totals.values()].sort((a, b) => b.current - a.current);
}

function delta(current: number, previous: number): string {
  if (previous === 0) return current === 0 ? "no change" : "new spend";
  const change = ((current - previous) / previous) * 100;
  return `${change > 0 ? "+" : ""}${change.toLocaleString("en-US", {
    maximumFractionDigits: 1,
  })}%`;
}

function resourceName(row: Row): string {
  if (row.resource_name) return String(row.resource_name);
  const type = String(row.resource_type ?? "");
  if (type === "unattributed") return "Not attributed to a workload";
  return `Unattributed ${type.replace(/_/g, " ")}`;
}

function tagSummary(row: Row): string {
  const tags = row.tags;
  if (!tags || typeof tags !== "object" || Array.isArray(tags)) return "No tags";
  const pairs = Object.entries(tags)
    .filter(([, value]) => value != null && String(value).trim() !== "")
    .slice(0, 3)
    .map(([key, value]) => `${key}: ${value}`);
  return pairs.length > 0 ? pairs.join(", ") : "No tags";
}

export function ProductSpendBreakdown({ rows, days }: { rows: Row[]; days: number }) {
  const products = useMemo(() => aggregateProductSpend(rows), [rows]);
  const [chosen, setChosen] = useState<string | null>(null);
  const currentTotal = products.reduce((sum, product) => sum + product.current, 0);
  const previousTotal = products.reduce((sum, product) => sum + product.previous, 0);
  const top = products.length > 8 ? products.slice(0, 7) : products;
  const remainder = products.length > 8 ? products.slice(7) : [];
  const groups = [
    ...top.map((product) => ({ ...product, productKeys: [product.key] })),
    ...(remainder.length
      ? [
          {
            key: "__OTHER__",
            label: `Other (${remainder.length})`,
            current: remainder.reduce((sum, product) => sum + product.current, 0),
            previous: remainder.reduce((sum, product) => sum + product.previous, 0),
            productKeys: remainder.map((product) => product.key),
          },
        ]
      : []),
  ];
  const selected = groups.find((group) => group.key === chosen) ?? groups[0];
  const max = Math.max(...groups.map((group) => group.current), 1);

  const detailRows = useMemo(() => {
    if (!selected) return [];
    const details = new Map<
      string,
      {
        product: string;
        workload: string;
        sku: string;
        tags: string;
        usage: number;
        unit: string;
        cost: number;
      }
    >();
    for (const row of rows) {
      if (row.period === "previous" || !selected.productKeys.includes(productKey(row.product))) {
        continue;
      }
      const product = productLabel(row.product);
      const workload = resourceName(row);
      const sku = String(row.sku_name ?? "Unknown SKU");
      const tags = tagSummary(row);
      const unit = String(row.usage_unit ?? "");
      const key = `${product}\u0000${workload}\u0000${sku}\u0000${tags}\u0000${unit}`;
      const existing = details.get(key) ?? {
        product,
        workload,
        sku,
        tags,
        usage: 0,
        unit,
        cost: 0,
      };
      const usage = Number(row.usage_quantity ?? 0);
      const cost = Number(row.list_cost_usd ?? 0);
      existing.usage += Number.isFinite(usage) ? usage : 0;
      existing.cost += Number.isFinite(cost) ? cost : 0;
      details.set(key, existing);
    }
    return [...details.values()].sort((a, b) => b.cost - a.cost);
  }, [rows, selected]);

  const hasUnpricedUsage = rows.some(
    (row) => row.period !== "previous" && Number(row.unpriced_usage_quantity ?? 0) > 0,
  );

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-2 border-b border-grid pb-3">
        <div>
          <p className="text-xs text-muted">Workspace list cost</p>
          <p className="text-2xl font-semibold tabular-nums text-ink">{usd(currentTotal)}</p>
        </div>
        <p className="text-xs text-muted">
          {delta(currentTotal, previousTotal)} vs previous {days} days
        </p>
      </div>

      {currentTotal > 0 && groups.length > 1 && (
        <div>
          <span
            className="flex h-3 w-full overflow-hidden rounded-md border border-grid"
            role="img"
            aria-label={`Spend composition: ${groups
              .filter((group) => group.current > 0)
              .map(
                (group) =>
                  `${group.label} ${((group.current / currentTotal) * 100).toFixed(0)}%`,
              )
              .join(", ")}`}
          >
            {groups.map((group, i) =>
              group.current > 0 ? (
                <span
                  key={group.key}
                  className={SERIES_FILLS[i % SERIES_FILLS.length]}
                  style={{ width: `${(group.current / currentTotal) * 100}%` }}
                />
              ) : null,
            )}
          </span>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1" aria-hidden="true">
            {groups.map((group, i) =>
              (group.current / currentTotal) * 100 >= 1 ? (
                <span
                  key={group.key}
                  className="inline-flex items-center gap-1.5 text-[11px] text-muted"
                >
                  <span
                    className={`h-2 w-2 rounded-sm ${SERIES_FILLS[i % SERIES_FILLS.length]}`}
                  />
                  {group.label}
                </span>
              ) : null,
            )}
          </div>
        </div>
      )}

      <ul className="space-y-1" aria-label="List cost by Databricks product">
        {groups.map((group, gi) => {
          const share = currentTotal > 0 ? (group.current / currentTotal) * 100 : 0;
          const isSelected = selected?.key === group.key;
          return (
            <li key={group.key}>
              <button
                type="button"
                aria-pressed={isSelected}
                onClick={() => setChosen(group.key)}
                className={`w-full rounded-lg px-2 py-2 text-left transition ${
                  isSelected ? "bg-hairline" : "hover:bg-hairline/50"
                }`}
              >
                <span className="mb-1 flex items-baseline justify-between gap-3 text-xs">
                  <span className="min-w-0 truncate font-medium text-ink-2">{group.label}</span>
                  <span className="flex shrink-0 items-baseline gap-3 tabular-nums">
                    <span className="text-muted">{share.toFixed(1)}%</span>
                    <span className="text-ink">{usd(group.current)}</span>
                  </span>
                </span>
                <span className="block h-2 rounded-sm bg-hairline" aria-hidden="true">
                  <span
                    className={`block h-2 rounded-sm ${SERIES_FILLS[gi % SERIES_FILLS.length]}`}
                    style={{ width: `${Math.max((group.current / max) * 100, 1)}%` }}
                  />
                </span>
                <span className="mt-1 block text-[11px] text-muted">
                  {delta(group.current, group.previous)} vs prior period
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      {selected && (
        <div>
          <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="text-xs font-semibold text-ink">{selected.label} cost drivers</h3>
            <span className="text-[11px] text-muted">
              Current {days}-day period · grouped by workload, SKU, and tags
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-grid text-muted">
                <tr>
                  {selected.key === "__OTHER__" && <th className="px-2 py-2 font-medium">Product</th>}
                  <th className="px-2 py-2 font-medium">Workload</th>
                  <th className="px-2 py-2 font-medium">SKU</th>
                  <th className="px-2 py-2 font-medium">Tags</th>
                  <th className="px-2 py-2 text-right font-medium">Usage</th>
                  <th className="px-2 py-2 text-right font-medium">List cost</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-grid/60">
                {detailRows.slice(0, 10).map((row) => (
                  <tr key={`${row.product}-${row.workload}-${row.sku}-${row.unit}`}>
                    {selected.key === "__OTHER__" && (
                      <td className="px-2 py-2 text-ink-2">{row.product}</td>
                    )}
                    <td className="max-w-52 truncate px-2 py-2 text-ink-2">{row.workload}</td>
                    <td className="max-w-72 truncate px-2 py-2 text-muted">{row.sku}</td>
                    <td className="max-w-64 truncate px-2 py-2 text-muted">{row.tags}</td>
                    <td className="whitespace-nowrap px-2 py-2 text-right tabular-nums text-muted">
                      {num(row.usage)} {row.unit}
                    </td>
                    <td className="whitespace-nowrap px-2 py-2 text-right tabular-nums text-ink">
                      {usd(row.cost)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {detailRows.length > 10 && (
            <p className="mt-2 text-[11px] text-muted">
              Top 10 of {detailRows.length} workload and SKU combinations.
            </p>
          )}
        </div>
      )}

      {hasUnpricedUsage && (
        <p className="text-[11px] text-status-warning">
          Some usage had no matching list price and is excluded from cost totals.
        </p>
      )}
    </div>
  );
}

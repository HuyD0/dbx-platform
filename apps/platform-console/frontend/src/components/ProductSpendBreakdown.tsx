import { useId, useMemo, useState } from "react";
import { currency as formatCurrency, num, usd } from "../lib/format";
import type { Row } from "../lib/types";

type ProductTotal = {
  key: string;
  label: string;
  current: number;
  previous: number;
};

export type FoundrySourceState = {
  status: "loading" | "ready" | "unavailable" | "error";
  rows: Row[];
  message?: string;
};

export type CostAttributionFlow = {
  key: string;
  source: "Databricks DBUs" | "Microsoft Foundry API Tokens";
  initiative: string;
  target: string;
  cost: number;
  currency: string;
};

export type SankeyNode = {
  key: string;
  label: string;
  cost: number;
};

export type SankeyLink = {
  key: string;
  from: string;
  to: string;
  cost: number;
};

export type CurrencySankey = {
  currency: string;
  total: number;
  sources: SankeyNode[];
  initiatives: SankeyNode[];
  targets: SankeyNode[];
  sourceLinks: SankeyLink[];
  targetLinks: SankeyLink[];
};

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
  if (tags && typeof tags === "object" && !Array.isArray(tags)) {
    const pairs = Object.entries(tags)
      .filter(([, value]) => value != null && String(value).trim() !== "")
      .slice(0, 3)
      .map(([key, value]) => `${key}: ${value}`);
    if (pairs.length > 0) return pairs.join(", ");
  }
  const attribution = [
    ["team", row.team],
    ["project", row.project],
  ]
    .filter(([, value]) => meaningful(value) !== null)
    .map(([key, value]) => `${key}: ${value}`);
  return attribution.length > 0 ? attribution.join(", ") : "No tags";
}

function tagValues(row: Row): Record<string, unknown> {
  return row.tags && typeof row.tags === "object" && !Array.isArray(row.tags)
    ? (row.tags as Record<string, unknown>)
    : {};
}

function meaningful(value: unknown): string | null {
  const text = String(value ?? "").trim();
  if (!text) return null;
  const normalized = text.toLowerCase().replace(/[\s_-]+/g, " ");
  if (["unallocated", "unattributed", "unknown", "none", "n/a"].includes(normalized)) {
    return null;
  }
  return text;
}

function initiativeEntity(value: string): string {
  if (/ms[-_ ]iva/i.test(value)) return "MS-IVA";
  if (/investment[-_ ]analytics/i.test(value)) return "Investment Analytics";
  const cleaned = value.trim().replace(/^rg[-_ ]+/i, "").replace(/[_-]+/g, " ");
  if (/^[A-Z0-9 -]+$/.test(value) && !/^RG[-_ ]/i.test(value)) return value;
  return cleaned.replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function initiativeLabel(row: Row): string {
  const tags = tagValues(row);
  const direct = [
    row.initiative,
    row.project,
    tags.initiative,
    tags.project,
    row.resource_group,
    row.team,
    tags.team,
    row.cost_center,
    tags.cost_center,
  ]
    .map(meaningful)
    .find((value): value is string => value !== null);
  return direct ? initiativeEntity(direct) : "Unallocated initiative";
}

function isDatabricksAiRow(row: Row): boolean {
  const product = productKey(row.product);
  if (["MODEL_SERVING", "VECTOR_SEARCH", "AI_GATEWAY"].includes(product)) {
    return true;
  }
  return String(row.resource_type ?? "").toLowerCase() === "endpoint";
}

function resourceLeaf(value: unknown): string | null {
  const text = meaningful(value);
  if (!text) return null;
  const parts = text.split("/").filter(Boolean);
  const leaf = parts.at(-1) ?? text;
  try {
    return decodeURIComponent(leaf);
  } catch {
    return leaf;
  }
}

function firstTarget(row: Row): string | null {
  return [
    row.deployment_name,
    row.deployment,
    row.model_name,
    row.index_name,
    row.vector_index_name,
    row.endpoint_name,
    row.serving_endpoint_name,
    row.resource_name,
  ]
    .map(meaningful)
    .find((value): value is string => value !== null) ?? null;
}

function databricksTargetLabel(row: Row): string {
  const product = productLabel(row.product);
  const target = firstTarget(row);
  if (!target || target.toLowerCase() === product.toLowerCase()) return `Shared ${product}`;
  return `${target} · ${product}`;
}

function foundryTargetLabel(row: Row): string {
  const explicit = firstTarget(row);
  const account = resourceLeaf(row.resource_id);
  const meter = meaningful(row.meter_name);
  const direction = meter?.search(/\b(input|output|cached|prompt|completion)\b/i) ?? -1;
  const model = direction > 0 ? meaningful(meter?.slice(0, direction)) : null;
  if (explicit) return account && explicit !== account ? `${explicit} · ${account}` : explicit;
  if (model) return account ? `${model} · ${account}` : model;
  return account ?? "Unallocated Foundry token resource";
}

function addFlow(
  flows: Map<string, CostAttributionFlow>,
  source: CostAttributionFlow["source"],
  initiative: string,
  target: string,
  cost: number,
  currency: string,
) {
  if (!Number.isFinite(cost) || cost <= 0) return;
  const normalizedCurrency = meaningful(currency)?.toUpperCase() ?? "UNRESOLVED";
  const key = `${normalizedCurrency}\u0000${source}\u0000${initiative}\u0000${target}`;
  const existing = flows.get(key);
  if (existing) existing.cost += cost;
  else {
    flows.set(key, {
      key,
      source,
      initiative,
      target,
      cost,
      currency: normalizedCurrency,
    });
  }
}

/** Build only valid AI-serving attribution paths. Ordinary Databricks Jobs,
 * SQL, Apps, and databases remain in the product breakdown but never enter
 * the deployed-model/index stage. */
export function buildCostAttributionFlows(
  databricksRows: Row[],
  foundryRows: Row[] = [],
): CostAttributionFlow[] {
  const flows = new Map<string, CostAttributionFlow>();
  for (const row of databricksRows) {
    if (String(row.period ?? "current").toLowerCase() === "previous") continue;
    if (!isDatabricksAiRow(row)) continue;
    addFlow(
      flows,
      "Databricks DBUs",
      initiativeLabel(row),
      databricksTargetLabel(row),
      Number(row.list_cost_usd ?? 0),
      "USD",
    );
  }
  for (const row of foundryRows) {
    if (!String(row.meter_name ?? "").toLowerCase().includes("token")) continue;
    addFlow(
      flows,
      "Microsoft Foundry API Tokens",
      initiativeLabel(row),
      foundryTargetLabel(row),
      Number(row.cost ?? 0),
      String(row.currency ?? "UNRESOLVED"),
    );
  }
  return [...flows.values()].sort(
    (a, b) => a.currency.localeCompare(b.currency) || b.cost - a.cost,
  );
}

function aggregateNodes(
  flows: CostAttributionFlow[],
  field: "source" | "initiative" | "target",
): SankeyNode[] {
  const totals = new Map<string, number>();
  for (const flow of flows) totals.set(flow[field], (totals.get(flow[field]) ?? 0) + flow.cost);
  return [...totals.entries()]
    .map(([label, cost]) => ({ key: label, label, cost }))
    .sort((a, b) => b.cost - a.cost || a.label.localeCompare(b.label));
}

function aggregateLinks(
  flows: CostAttributionFlow[],
  from: "source" | "initiative",
  to: "initiative" | "target",
): SankeyLink[] {
  const links = new Map<string, SankeyLink>();
  for (const flow of flows) {
    const key = `${flow[from]}\u0000${flow[to]}`;
    const existing = links.get(key);
    if (existing) existing.cost += flow.cost;
    else links.set(key, { key, from: flow[from], to: flow[to], cost: flow.cost });
  }
  return [...links.values()].sort(
    (a, b) => b.cost - a.cost || a.from.localeCompare(b.from) || a.to.localeCompare(b.to),
  );
}

/** Aggregate unique nodes and links inside strict currency partitions. */
export function buildCurrencySankeys(flows: CostAttributionFlow[]): CurrencySankey[] {
  const partitions = new Map<string, CostAttributionFlow[]>();
  for (const flow of flows) {
    const rows = partitions.get(flow.currency) ?? [];
    rows.push(flow);
    partitions.set(flow.currency, rows);
  }
  return [...partitions.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([currency, rows]) => ({
      currency,
      total: rows.reduce((sum, row) => sum + row.cost, 0),
      sources: aggregateNodes(rows, "source"),
      initiatives: aggregateNodes(rows, "initiative"),
      targets: aggregateNodes(rows, "target"),
      sourceLinks: aggregateLinks(rows, "source", "initiative"),
      targetLinks: aggregateLinks(rows, "initiative", "target"),
    }));
}

function amountsForSource(
  flows: CostAttributionFlow[],
  source: CostAttributionFlow["source"],
) {
  const totals = new Map<string, number>();
  for (const flow of flows) {
    if (flow.source !== source) continue;
    totals.set(flow.currency, (totals.get(flow.currency) ?? 0) + flow.cost);
  }
  return [...totals.entries()].sort(([a], [b]) => a.localeCompare(b));
}

function SourceCoverage({
  source,
  status,
  message,
  amounts,
  tone,
}: {
  source: CostAttributionFlow["source"];
  status: FoundrySourceState["status"] | "ready";
  message: string;
  amounts: [string, number][];
  tone: "gold" | "teal";
}) {
  const unavailable = status === "unavailable" || status === "error";
  return (
    <div
      className={`rounded-lg border px-3 py-2 ${
        unavailable
          ? "border-brand-primary bg-critical-surface"
          : tone === "gold"
            ? "border-warning-accent bg-warning-surface"
            : "border-status-info/30 bg-info-surface"
      }`}
      role={status === "ready" ? undefined : "status"}
      aria-busy={status === "loading" || undefined}
    >
      <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted">
        Infrastructure source
      </p>
      <div className="mt-1 flex flex-wrap items-start justify-between gap-2">
        <span className="text-xs font-semibold text-ink">{source}</span>
        <span
          className={`text-[11px] font-semibold ${
            status === "error"
              ? "text-status-critical"
              : status === "loading"
                ? "text-status-warning"
                : status === "unavailable"
                  ? "text-status-serious"
                  : "text-status-good"
          }`}
        >
          {status === "loading"
            ? "Loading"
            : status === "error"
              ? "Error"
              : status === "unavailable"
                ? "Unavailable"
                : "Available"}
        </span>
      </div>
      <p className="mt-1 text-[11px] leading-4 text-muted">{message}</p>
      {amounts.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-1.5" aria-label={`${source} totals by currency`}>
          {amounts.map(([currency, cost]) => (
            <li
              key={currency}
              className="rounded-full border border-grid bg-surface px-2 py-0.5 text-[11px] tabular-nums text-ink-2"
            >
              {formatCurrency(cost, currency)} attributed
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function NodeColumn({
  title,
  nodes,
  total,
  currency,
  tone,
}: {
  title: string;
  nodes: SankeyNode[];
  total: number;
  currency: string;
  tone: "gold" | "rose" | "teal";
}) {
  const styles = {
    gold: "border-warning-accent bg-warning-surface",
    rose: "border-brand-mid/30 bg-tint",
    teal: "border-status-info/30 bg-info-surface",
  };
  const bars = {
    gold: "bg-warning-accent",
    rose: "bg-brand-mid",
    teal: "bg-series-1",
  };
  return (
    <div role="group" aria-label={title}>
      <h4 className="mb-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted">
        {title}
      </h4>
      <ul className="space-y-2">
        {nodes.map((node) => {
          const share = total > 0 ? (node.cost / total) * 100 : 0;
          return (
            <li key={node.key} className={`rounded-lg border p-2.5 ${styles[tone]}`}>
              <div className="flex items-start justify-between gap-2 text-xs">
                <span className="min-w-0 break-words font-semibold text-ink">{node.label}</span>
                <span className="shrink-0 tabular-nums text-ink-2">
                  {formatCurrency(node.cost, currency)}
                </span>
              </div>
              <div className="mt-2 h-1.5 rounded-full bg-surface" aria-hidden="true">
                <div
                  className={`h-1.5 rounded-full ${bars[tone]}`}
                  style={{ width: `${Math.max(share, 2)}%` }}
                />
              </div>
              <span className="mt-1 block text-[10px] tabular-nums text-muted">
                {share.toFixed(1)}% of this currency partition
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function LinkColumn({
  title,
  links,
  total,
  currency,
  tone,
}: {
  title: string;
  links: SankeyLink[];
  total: number;
  currency: string;
  tone: "gold" | "teal";
}) {
  return (
    <div role="group" aria-label={title} className="md:pt-6">
      <h4 className="mb-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted md:sr-only">
        {title}
      </h4>
      <ol className="space-y-2">
        {links.map((link) => {
          const share = total > 0 ? (link.cost / total) * 100 : 0;
          const thickness = Math.max(3, Math.min(12, share / 6));
          return (
            <li
              key={link.key}
              aria-label={`${link.from} to ${link.to}: ${formatCurrency(link.cost, currency)}`}
              className="rounded-lg border border-grid bg-surface px-2 py-2"
            >
              <span className="block text-[10px] leading-4 text-muted">
                <span className="font-medium text-ink-2">{link.from}</span>
                <span aria-hidden="true"> → </span>
                <span className="font-medium text-ink-2">{link.to}</span>
              </span>
              <div className="mt-1.5 flex items-center gap-2">
                <span className="flex h-3 flex-1 items-center rounded-full bg-page">
                  <span
                    className={`block rounded-full ${
                      tone === "gold" ? "bg-warning-accent" : "bg-series-1"
                    }`}
                    style={{ width: `${Math.max(share, 2)}%`, height: `${thickness}px` }}
                  />
                </span>
                <span className="shrink-0 text-[10px] tabular-nums text-muted">
                  {formatCurrency(link.cost, currency)}
                </span>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function CostAttributionChart({
  databricksRows,
  foundry,
}: {
  databricksRows: Row[];
  foundry: FoundrySourceState;
}) {
  const captionId = useId();
  const foundryRows = foundry.status === "ready" ? foundry.rows : [];
  const flows = useMemo(
    () => buildCostAttributionFlows(databricksRows, foundryRows),
    [databricksRows, foundryRows],
  );
  const sankeys = useMemo(() => buildCurrencySankeys(flows), [flows]);
  const databricksAmounts = amountsForSource(flows, "Databricks DBUs");
  const foundryAmounts = amountsForSource(flows, "Microsoft Foundry API Tokens");
  const foundryMessage =
    foundry.message ??
    (foundry.status === "loading"
      ? "Loading persisted Azure resource and meter actuals."
      : foundry.status === "ready"
        ? foundry.rows.length > 0
          ? "Persisted Azure token-meter actuals, kept in their original billing currencies."
          : "The source is available; no Foundry billed cost was recorded in this window."
        : foundry.status === "unavailable"
          ? "Persisted Foundry resource/meter actuals are not available for this scope."
          : "Foundry actuals could not be read.");

  return (
    <figure
      aria-labelledby={captionId}
      data-testid="cost-attribution-flow"
      className="rounded-xl border border-grid bg-page/40 p-3 sm:p-4"
    >
      <figcaption id={captionId} className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-brand-maroon">Cross-cloud cost attribution</h3>
          <p className="mt-0.5 text-xs text-muted">
            AI-serving spend only, aggregated from infrastructure through initiatives to model and
            index targets. Foundry labels are resource/meter hints, not proof of exact deployment
            allocation. Currency partitions never mix.
          </p>
        </div>
        <span className="rounded-full border border-grid bg-surface px-2.5 py-1 text-[11px] text-muted">
          Current period
        </span>
      </figcaption>

      <div className="mt-3 grid gap-2 sm:grid-cols-2" aria-label="Infrastructure source status">
        <SourceCoverage
          source="Databricks DBUs"
          status="ready"
          message="Databricks list-price rows for model serving, vector search, AI Gateway, and endpoint-attributed usage."
          amounts={databricksAmounts}
          tone="gold"
        />
        <SourceCoverage
          source="Microsoft Foundry API Tokens"
          status={foundry.status}
          message={foundryMessage}
          amounts={foundryAmounts}
          tone="teal"
        />
      </div>

      {sankeys.length === 0 ? (
        <p className="mt-3 rounded-lg border border-dashed border-grid p-3 text-xs text-muted">
          No attributable AI-serving spend is available for the current period.
        </p>
      ) : (
        <div className="mt-4 space-y-4" aria-label="Attributed cost Sankey partitions">
          {sankeys.map((partition) => (
            <section
              key={partition.currency}
              aria-label={`${partition.currency} cost attribution`}
              className="rounded-xl border border-grid bg-surface p-3"
            >
              <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
                <h3 className="text-xs font-semibold text-brand-maroon">
                  {partition.currency} attribution flow
                </h3>
                <span className="text-xs tabular-nums text-ink-2">
                  {formatCurrency(partition.total, partition.currency)} total in this currency
                </span>
              </div>
              <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(9rem,0.85fr)_minmax(0,1fr)_minmax(9rem,0.85fr)_minmax(0,1fr)]">
                <NodeColumn
                  title="Infrastructure sources"
                  nodes={partition.sources}
                  total={partition.total}
                  currency={partition.currency}
                  tone="gold"
                />
                <LinkColumn
                  title="Source to initiative links"
                  links={partition.sourceLinks}
                  total={partition.total}
                  currency={partition.currency}
                  tone="gold"
                />
                <NodeColumn
                  title="Internal initiatives"
                  nodes={partition.initiatives}
                  total={partition.total}
                  currency={partition.currency}
                  tone="rose"
                />
                <LinkColumn
                  title="Initiative to deployment links"
                  links={partition.targetLinks}
                  total={partition.total}
                  currency={partition.currency}
                  tone="teal"
                />
                <NodeColumn
                  title="Deployed models / indexes"
                  nodes={partition.targets}
                  total={partition.total}
                  currency={partition.currency}
                  tone="teal"
                />
              </div>
            </section>
          ))}
        </div>
      )}
    </figure>
  );
}

const DEFAULT_FOUNDRY_SOURCE: FoundrySourceState = {
  status: "unavailable",
  rows: [],
};

export function ProductSpendBreakdown({
  rows,
  days,
  foundrySource = DEFAULT_FOUNDRY_SOURCE,
}: {
  rows: Row[];
  days: number;
  foundrySource?: FoundrySourceState;
}) {
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

      <CostAttributionChart databricksRows={rows} foundry={foundrySource} />

      {groups.length === 0 ? (
        <p className="rounded-lg border border-dashed border-grid p-3 text-xs text-muted">
          No Databricks product list-cost rows are available in this window.
        </p>
      ) : (
        <ul className="space-y-1" aria-label="List cost by Databricks product">
          {groups.map((group) => {
            const share = currentTotal > 0 ? (group.current / currentTotal) * 100 : 0;
            const isSelected = selected?.key === group.key;
            return (
              <li key={group.key}>
                <button
                  type="button"
                  aria-pressed={isSelected}
                  onClick={() => setChosen(group.key)}
                  className={`w-full rounded-lg px-2 py-2 text-left transition ${
                    isSelected ? "bg-tint" : "hover:bg-tint/60"
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
                      className="block h-2 rounded-sm bg-series-1"
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
      )}

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
              <thead className="border-b border-grid bg-warning-surface text-brand-maroon">
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
                  <tr key={`${row.product}-${row.workload}-${row.sku}-${row.unit}`} className="hover:bg-tint/50">
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

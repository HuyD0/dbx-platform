import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Bot,
  Boxes,
  ChevronRight,
  Cloud,
  Database,
  Download,
  Search,
  X,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type ReactNode,
} from "react";
import { useSearchParams } from "react-router-dom";
import { apiGet, isUnavailable } from "../lib/api";
import {
  accessLookupKey,
  groupModelInventory,
  inventoryIssues,
  isDatabricksSystemModel,
  modelDisplayName,
  normalizeModelInventory,
  parseModelDetails,
  sourceLabel,
  type InventoryFacet,
  type ModelInventoryGroup,
  type ModelInventoryPayload,
  type ModelInventoryResponse,
  type ModelInventoryRow,
} from "../lib/modelInventory";
import type { Envelope, Row, SourceHealth } from "../lib/types";
import { DataTable } from "./DataTable";
import {
  AsOf,
  Badge,
  CapabilityNotice,
  Card,
  EmptyState,
  ErrorState,
  SectionTitle,
  Skeleton,
  statusTone,
} from "./ui";

type InventoryView = "explorer" | "audit";
type DetailTab = "overview" | "cost" | "access" | "compliance" | "history" | "raw";

const SOURCES = ["databricks_uc", "databricks_serving", "azure_openai"] as const;
const SOURCE_ICONS: Record<string, typeof Bot> = {
  databricks_uc: Database,
  databricks_serving: Bot,
  azure_openai: Cloud,
};
const SOURCE_ORDER = new Map(SOURCES.map((source, index) => [source, index]));

function normalizeAccessRows(response: unknown): Row[] {
  if (Array.isArray(response)) return response as Row[];
  if (!response || typeof response !== "object") return [];
  const envelope = response as { data?: unknown };
  if (Array.isArray(envelope.data)) return envelope.data as Row[];
  if (envelope.data && typeof envelope.data === "object") {
    const payload = envelope.data as { items?: unknown; rows?: unknown };
    if (Array.isArray(payload.items)) return payload.items as Row[];
    if (Array.isArray(payload.rows)) return payload.rows as Row[];
  }
  return [];
}

function SourceHealthBanner({
  health,
  rows,
  truncated,
  total,
}: {
  health: SourceHealth[];
  rows: ModelInventoryRow[];
  truncated: boolean;
  total: number;
}) {
  const counts = useMemo(() => {
    const values = new Map<string, number>();
    rows.forEach((row) => {
      const source = String(row.source ?? "unknown");
      values.set(source, (values.get(source) ?? 0) + 1);
    });
    return Array.from(values.entries()).sort(
      ([left], [right]) =>
        (SOURCE_ORDER.get(left as (typeof SOURCES)[number]) ?? 99) -
        (SOURCE_ORDER.get(right as (typeof SOURCES)[number]) ?? 99),
    );
  }, [rows]);

  return (
    <div
      className="mb-4 rounded-xl border border-grid bg-page/35 p-3"
      aria-label="Inventory source coverage"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-xs font-semibold text-ink">Inventory source coverage</p>
          <p className="mt-0.5 text-[11px] leading-4 text-muted">
            Missing or partial sources stay unknown; their absence is never treated as healthy.
          </p>
        </div>
        {truncated && (
          <Badge tone="warning">
            Showing {rows.length} of {total}
          </Badge>
        )}
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        {health.length > 0
          ? health.map((source) => (
              <span
                key={`${source.source}-${source.status}`}
                className="inline-flex items-center gap-1.5 rounded-lg border border-grid bg-surface px-2 py-1 text-[11px] text-ink-2"
                title={source.notes ?? undefined}
              >
                <span>{sourceLabel(source.source)}</span>
                <Badge tone={statusTone(source.status)}>{source.status}</Badge>
              </span>
            ))
          : counts.map(([source, count]) => (
              <span
                key={source}
                className="inline-flex items-center gap-1.5 rounded-lg border border-grid bg-surface px-2 py-1 text-[11px] text-ink-2"
              >
                <span>{sourceLabel(source)}</span>
                <span className="tabular-nums text-muted">{count} observed</span>
              </span>
            ))}
        {health.length === 0 && (
          <span className="inline-flex items-center gap-1 text-[11px] text-status-warning">
            <AlertTriangle className="h-3 w-3" />
            Per-source health not reported
          </span>
        )}
      </div>
      {health.some((source) => source.status !== "healthy" && source.notes) && (
        <ul className="mt-2 space-y-1 text-[11px] leading-4 text-status-warning">
          {health
            .filter((source) => source.status !== "healthy" && source.notes)
            .map((source) => (
              <li key={`${source.source}-${source.status}-note`}>
                <span className="font-medium">{sourceLabel(source.source)}:</span>{" "}
                {source.notes}
              </li>
            ))}
        </ul>
      )}
    </div>
  );
}

function SummaryTile({
  label,
  value,
  hint,
}: {
  label: string;
  value: number;
  hint: string;
}) {
  return (
    <div className="rounded-xl border border-grid bg-page/30 p-3">
      <p className="text-[11px] font-medium text-muted">{label}</p>
      <p className="mt-0.5 text-2xl font-semibold tabular-nums text-ink">{value}</p>
      <p className="mt-0.5 text-[10px] leading-4 text-muted">{hint}</p>
    </div>
  );
}

function GroupCard({
  group,
  onSelect,
}: {
  group: ModelInventoryGroup;
  onSelect: (row: ModelInventoryRow, event: MouseEvent<HTMLButtonElement>) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const Icon = SOURCE_ICONS[group.source] ?? Boxes;
  const primary = group.rows[0];
  const issueLabels = Array.from(new Set(group.rows.flatMap(inventoryIssues)));
  const shownRows = expanded ? group.rows : group.rows.slice(0, 4);
  return (
    <article className="rounded-xl border border-grid bg-page/25 p-3 hover:bg-hairline/40">
      <div className="flex items-start gap-3">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-hairline text-accent">
          <Icon className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-ink" title={group.label}>
                {group.label}
              </p>
              <p className="mt-0.5 text-[11px] text-muted">
                {sourceLabel(group.source)}
                {group.rows.length > 1 ? ` · ${group.rows.length} entities` : ""}
              </p>
            </div>
            {issueLabels.length > 0 ? (
              <Badge tone="warning">{issueLabels.length} needs attention</Badge>
            ) : (
              <Badge tone="good">No explicit risk</Badge>
            )}
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5 text-[11px] text-muted">
            {primary.provider && <span>{primary.provider}</span>}
            {primary.environment && <span>· {primary.environment}</span>}
            {primary.region && <span>· {primary.region}</span>}
            {primary.owner && <span>· Owner: {primary.owner}</span>}
          </div>
          {issueLabels.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {issueLabels.slice(0, 3).map((issue) => (
                <span
                  key={issue}
                  className="rounded-md bg-warning-surface px-1.5 py-0.5 text-[10px] font-medium text-status-warning"
                >
                  {issue}
                </span>
              ))}
            </div>
          )}
          <div className="mt-3 divide-y divide-grid/60 rounded-lg border border-grid/70 bg-surface">
            {shownRows.map((row) => (
              <button
                key={String(row.model_key ?? row.resource_id ?? modelDisplayName(row))}
                type="button"
                data-inventory-key={String(row.model_key ?? "")}
                onClick={(event) => onSelect(row, event)}
                className="flex w-full items-center gap-2 px-2.5 py-2 text-left text-xs hover:bg-hairline"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium text-ink-2">
                    {modelDisplayName(row)}
                  </span>
                  <span className="block truncate text-[10px] text-muted">
                    {row.entity_type || "Model"}
                    {row.model_version ? ` · v${row.model_version}` : ""}
                  </span>
                </span>
                {inventoryIssues(row).length > 0 && (
                  <AlertTriangle
                    className="h-3.5 w-3.5 shrink-0 text-status-warning"
                    aria-label="Needs attention"
                  />
                )}
                <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted" />
              </button>
            ))}
            {group.rows.length > 4 && (
              <button
                type="button"
                onClick={() => setExpanded((value) => !value)}
                className="w-full px-2.5 py-2 text-left text-[10px] font-medium text-accent hover:bg-hairline"
                aria-expanded={expanded}
              >
                {expanded
                  ? "Show fewer entities"
                  : `Show ${group.rows.length - 4} more entities`}
              </button>
            )}
          </div>
        </div>
      </div>
    </article>
  );
}

function DetailPair({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-[10px] font-semibold uppercase tracking-wide text-muted">{label}</dt>
      <dd className="mt-0.5 break-words text-xs text-ink-2">{children || "—"}</dd>
    </div>
  );
}

function EvidenceState({
  label,
  value,
  unknown = "Not attested",
}: {
  label: string;
  value: boolean | null;
  unknown?: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-grid p-2.5">
      <span className="text-xs text-ink-2">{label}</span>
      <Badge tone={value === true ? "good" : value === false ? "warning" : "info"}>
        {value === true ? "Enabled" : value === false ? "Disabled" : unknown}
      </Badge>
    </div>
  );
}

function bool(value: unknown): boolean | null {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  const normalized = String(value ?? "").toLowerCase();
  if (["true", "1", "yes", "enabled", "on"].includes(normalized)) return true;
  if (["false", "0", "no", "disabled", "off"].includes(normalized)) return false;
  return null;
}

function nestedValue(details: Row, keys: string[]): unknown {
  const sources = [details, details.tags, details.account_tags, details.governance].filter(
    (value): value is Row => Boolean(value) && typeof value === "object",
  );
  for (const source of sources) {
    for (const [key, value] of Object.entries(source)) {
      if (keys.includes(key.toLowerCase())) return value;
    }
  }
  return undefined;
}

function ModelDetailDrawer({
  modelKey,
  fallbackRow,
  returnFocus,
  onClose,
}: {
  modelKey: string;
  fallbackRow?: ModelInventoryRow;
  returnFocus: HTMLButtonElement | null;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<DetailTab>("overview");
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const titleId = useId();
  const panelId = useId();
  const detail = useQuery({
    queryKey: ["/api/ai-governance/inventory/detail", modelKey],
    queryFn: async () => {
      try {
        return await apiGet<
          Envelope<{
            entity: ModelInventoryRow;
            access: Row[];
            source_health: SourceHealth[];
          }>
        >(`/api/ai-governance/inventory/${encodeURIComponent(modelKey)}`);
      } catch (error) {
        if (!isUnavailable(error) || !fallbackRow) throw error;
        const accessKey = accessLookupKey(fallbackRow);
        const access = await apiGet<Envelope<Row[]> | Row[]>(
          "/api/ai-governance/access",
          { model_key: accessKey },
        );
        return {
          data: {
            entity: fallbackRow,
            access: normalizeAccessRows(access),
            source_health: [],
          },
          count: 1,
          as_of: "",
          cached: false,
        };
      }
    },
    staleTime: 60_000,
    retry: false,
  });
  const row = detail.data?.data.entity ?? fallbackRow;
  const accessRows = detail.data?.data.access ?? [];
  const sourceHealth = detail.data?.data.source_health ?? [];
  const details = row ? parseModelDetails(row) : {};
  const issues = row ? inventoryIssues(row) : [];
  const costFields = row
    ? Object.fromEntries(
        Object.entries(row).filter(([key, value]) => {
          const normalized = key.toLowerCase();
          return (
            value != null &&
            (normalized.includes("cost") ||
              normalized.includes("spend") ||
              normalized.includes("request") ||
              normalized.includes("token"))
          );
        }),
      )
    : {};

  const close = useCallback(() => {
    onClose();
    window.requestAnimationFrame(() => returnFocus?.focus());
  }, [onClose, returnFocus]);

  useEffect(() => {
    setTab("overview");
    closeRef.current?.focus();
  }, [modelKey]);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [close]);

  const tabs: Array<{ id: DetailTab; label: string }> = [
    { id: "overview", label: "Overview" },
    { id: "cost", label: "Cost & usage" },
    { id: "access", label: "Access" },
    { id: "compliance", label: "Compliance" },
    { id: "history", label: "History" },
    { id: "raw", label: "Raw evidence" },
  ];

  const moveTab = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const nextIndex =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? tabs.length - 1
          : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) %
            tabs.length;
    setTab(tabs[nextIndex].id);
    dialogRef.current
      ?.querySelectorAll<HTMLButtonElement>('[role="tab"]')
      .item(nextIndex)
      .focus();
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/25" onMouseDown={close}>
      <section
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(event) => event.stopPropagation()}
        className="h-full w-full max-w-2xl overflow-y-auto border-l border-grid bg-surface p-4 shadow-2xl sm:p-5"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-accent">
              {sourceLabel(row?.source)}
            </p>
            <h3 id={titleId} className="mt-1 break-words text-lg font-semibold text-ink">
              {row ? modelDisplayName(row) : "Model details"}
            </h3>
            <p className="mt-1 break-all font-mono text-[10px] text-muted">{modelKey}</p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={close}
            aria-label="Close model details"
            className="rounded-lg border border-grid p-2 text-muted hover:bg-hairline hover:text-ink"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {detail.isPending && !row ? (
          <div className="mt-4">
            <Skeleton rows={6} />
          </div>
        ) : detail.isError && !row ? (
          <div className="mt-4">
            <ErrorState error={detail.error} />
          </div>
        ) : row ? (
          <>
            <div
              role="tablist"
              aria-label="Model details"
              className="mt-4 flex gap-1 overflow-x-auto border-b border-grid pb-2"
            >
              {tabs.map((item, index) => (
                <button
                  key={item.id}
                  id={`${panelId}-${item.id}-tab`}
                  type="button"
                  role="tab"
                  aria-controls={`${panelId}-${item.id}`}
                  aria-selected={tab === item.id}
                  tabIndex={tab === item.id ? 0 : -1}
                  onClick={() => setTab(item.id)}
                  onKeyDown={(event) => moveTab(event, index)}
                  className={`shrink-0 rounded-lg px-2.5 py-1.5 text-xs font-medium ${
                    tab === item.id ? "bg-accent text-white" : "text-muted hover:bg-hairline"
                  }`}
                >
                  {item.label}
                </button>
              ))}
            </div>

            <div
              id={`${panelId}-${tab}`}
              role="tabpanel"
              aria-labelledby={`${panelId}-${tab}-tab`}
              className="mt-4"
            >
              {detail.isError && (
                <div className="mb-3">
                  <CapabilityNotice
                    title="Detail evidence is partial"
                    description="The inventory row is visible, but its dedicated detail or access evidence could not be loaded."
                  />
                </div>
              )}
              {tab === "overview" && (
                <div className="space-y-4">
                  {issues.length > 0 && (
                    <div className="rounded-xl border border-warning-accent bg-warning-surface p-3">
                      <p className="flex items-center gap-2 text-xs font-semibold text-status-warning">
                        <AlertTriangle className="h-4 w-4" />
                        Needs attention
                      </p>
                      <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-ink-2">
                        {issues.map((issue) => (
                          <li key={issue}>{issue}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  <dl className="grid gap-4 rounded-xl border border-grid bg-page/25 p-3 sm:grid-cols-2">
                    <DetailPair label="Entity">{row.entity_type}</DetailPair>
                    <DetailPair label="Provider">{row.provider}</DetailPair>
                    <DetailPair label="Version">{row.model_version}</DetailPair>
                    <DetailPair label="Status">{row.status}</DetailPair>
                    <DetailPair label="Endpoint">{row.endpoint_name}</DetailPair>
                    <DetailPair label="Owner">{row.owner}</DetailPair>
                    <DetailPair label="Environment">{row.environment}</DetailPair>
                    <DetailPair label="Region">{row.region}</DetailPair>
                    <DetailPair label="Resource group">{row.resource_group}</DetailPair>
                    <DetailPair label="Resource ID">{row.resource_id}</DetailPair>
                  </dl>
                  {sourceHealth.length > 0 && (
                    <div className="rounded-xl border border-grid bg-page/25 p-3">
                      <p className="text-xs font-semibold text-ink">Applicable source health</p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {sourceHealth.map((item) => (
                          <span
                            key={`${item.source}-${item.status}`}
                            className="inline-flex items-center gap-1.5 text-xs text-ink-2"
                          >
                            {sourceLabel(item.source)}
                            <Badge tone={statusTone(item.status)}>{item.status}</Badge>
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
              {tab === "cost" &&
                (Object.keys(costFields).length > 0 ? (
                  <DataTable
                    rows={[costFields]}
                    searchable={false}
                    exportable={false}
                    caption="Model cost and usage evidence"
                  />
                ) : (
                  <EmptyState
                    positive={false}
                    message="No model-level cost or usage evidence is attached to this catalog record."
                  />
                ))}
              {tab === "access" &&
                (detail.isPending ? (
                  <Skeleton rows={4} />
                ) : accessRows.length > 0 ? (
                  <DataTable
                    rows={accessRows}
                    maxRows={Number.MAX_SAFE_INTEGER}
                    exportName="ai-model-access"
                    caption="Model access evidence"
                  />
                ) : (
                  <EmptyState
                    positive={false}
                    message="No access grants were returned for this model scope."
                  />
                ))}
              {tab === "compliance" && (
                <div className="space-y-2">
                  <EvidenceState
                    label="Keyless authentication"
                    value={
                      bool(row.key_auth_enabled) === null
                        ? null
                        : !bool(row.key_auth_enabled)
                    }
                  />
                  <EvidenceState label="Usage tracking" value={bool(row.usage_tracking)} />
                  <EvidenceState
                    label="Zero Data Retention"
                    value={bool(
                      nestedValue(details, [
                        "zdr",
                        "zdr_enabled",
                        "zero_data_retention",
                        "zero_data_retention_enabled",
                      ]),
                    )}
                  />
                  <EvidenceState
                    label="Content safety"
                    value={bool(
                      nestedValue(details, [
                        "content_safety",
                        "content_safety_enabled",
                        "rai_policy_enabled",
                      ]),
                    )}
                  />
                </div>
              )}
              {tab === "history" && (
                <dl className="grid gap-4 rounded-xl border border-grid bg-page/25 p-3 sm:grid-cols-2">
                  <DetailPair label="First observed">{row.first_seen_at}</DetailPair>
                  <DetailPair label="Last observed">{row.last_seen_at}</DetailPair>
                  <DetailPair label="Current">{String(row.is_current ?? "unknown")}</DetailPair>
                  <DetailPair label="Evidence source">{sourceLabel(row.source)}</DetailPair>
                </dl>
              )}
              {tab === "raw" && (
                <pre className="max-h-[36rem] overflow-auto rounded-xl border border-grid bg-page/40 p-3 text-[11px] leading-5 text-ink-2">
                  {JSON.stringify(detail.data?.data ?? { entity: row }, null, 2)}
                </pre>
              )}
            </div>
          </>
        ) : null}
      </section>
    </div>
  );
}

const FACET_FILTERS = [
  ["source", "Source"],
  ["provider", "Provider"],
  ["environment", "Environment"],
  ["entity_type", "Entity type"],
  ["owner", "Owner"],
  ["status", "Status"],
  ["exposure", "Exposure"],
  ["risk", "Risk"],
] as const;

function facetOptions(
  facets: Record<string, InventoryFacet[]>,
  key: string,
): InventoryFacet[] {
  return facets[key] ?? [];
}

function localFacets(rows: ModelInventoryRow[]): Record<string, InventoryFacet[]> {
  return Object.fromEntries(
    FACET_FILTERS.map(([key]) => {
      const counts = new Map<string, number>();
      rows.forEach((row) => {
        const values =
          key === "exposure"
            ? (row.exposure ?? ["none_attested"])
            : [String(row[key] ?? "unknown")];
        values.forEach((value) => {
          const text = String(value || "unknown");
          counts.set(text, (counts.get(text) ?? 0) + 1);
        });
      });
      return [
        key,
        Array.from(counts, ([value, count]) => ({ value, count })).sort((left, right) =>
          left.value.localeCompare(right.value, undefined, { sensitivity: "base" }),
        ),
      ];
    }),
  );
}

export function ModelInventoryExplorer() {
  const queryClient = useQueryClient();
  const searchId = useId();
  const [params, setParams] = useSearchParams();
  const [returnFocus, setReturnFocus] = useState<HTMLButtonElement | null>(null);
  const presentation: InventoryView =
    params.get("inventoryView") === "audit" ? "audit" : "explorer";
  const search = params.get("q") ?? "";
  const includeSystem = params.get("system") === "1";
  const cursor = params.get("inventoryCursor") ?? "";
  const selectedKey = params.get("model");
  const filters = Object.fromEntries(
    FACET_FILTERS.map(([key]) => [key, params.get(key) ?? ""]),
  ) as Record<(typeof FACET_FILTERS)[number][0], string>;
  const serverView = includeSystem ? "all" : "managed_or_risky";

  const updateParam = useCallback(
    (key: string, value: string, resetCursor = true) => {
      const next = new URLSearchParams(params);
      if (value) next.set(key, value);
      else next.delete(key);
      if (resetCursor) next.delete("inventoryCursor");
      setParams(next, { replace: true });
    },
    [params, setParams],
  );

  const requestParams = useMemo(() => {
    const request: Record<string, string | number | boolean> = {
      view: serverView,
      limit: 50,
    };
    if (search) request.q = search;
    if (cursor) request.cursor = cursor;
    FACET_FILTERS.forEach(([key]) => {
      if (filters[key]) request[key] = filters[key];
    });
    return request;
  }, [cursor, filters, search, serverView]);

  const loadInventory = useCallback(
    async (refresh = false): Promise<ModelInventoryResponse> => {
      try {
        return await apiGet<ModelInventoryResponse>("/api/ai-governance/inventory", {
          ...requestParams,
          ...(refresh ? { refresh: true } : {}),
        });
      } catch (error) {
        if (!isUnavailable(error)) throw error;
        const legacyResponse = await apiGet<ModelInventoryResponse>(
          "/api/ai-governance/catalog",
          refresh ? { refresh: true } : undefined,
        );
        const legacy = normalizeModelInventory(legacyResponse);
        const allRows = legacy.rows.map((row) => ({
          ...row,
          ownership: isDatabricksSystemModel(row) ? "system" : "customer_managed",
          needs_attention: inventoryIssues(row).length > 0,
          risk: inventoryIssues(row).length > 0 ? "attention" : "clear",
          risk_reasons: inventoryIssues(row),
        }));
        const needle = search.trim().toLowerCase();
        const filtered = allRows.filter((row) => {
          if (
            serverView === "managed_or_risky" &&
            row.ownership === "system" &&
            !row.needs_attention
          ) {
            return false;
          }
          if (
            FACET_FILTERS.some(([key]) => {
              const expected = filters[key];
              if (!expected) return false;
              if (key === "exposure") return !(row.exposure ?? []).includes(expected);
              return String(row[key] ?? "").toLowerCase() !== expected.toLowerCase();
            })
          ) {
            return false;
          }
          if (!needle) return true;
          return [
            row.model_name,
            row.model_key,
            row.endpoint_name,
            row.owner,
            row.provider,
            row.resource_group,
            row.status,
          ].some((value) => String(value ?? "").toLowerCase().includes(needle));
        });
        const payload: ModelInventoryPayload = {
          items: filtered,
          total: filtered.length,
          next_cursor: null,
          truncated: legacy.truncated,
          facets: localFacets(allRows),
          summary: {
            total: allRows.length,
            customer_managed: allRows.filter((row) => row.ownership === "customer_managed")
              .length,
            system: allRows.filter((row) => row.ownership === "system").length,
            needs_attention: allRows.filter((row) => row.needs_attention).length,
            key_auth_exposed: allRows.filter((row) => bool(row.key_auth_enabled) === true)
              .length,
            groups_on_page: groupModelInventory(filtered).length,
          },
          source_health:
            legacy.sourceHealth.length > 0
              ? legacy.sourceHealth
              : [
                  {
                    source: "AI model catalog",
                    status: "partial",
                    notes:
                      "The new inventory API is not deployed; this is the legacy catalog snapshot.",
                  },
                ],
          legacy_fallback: true,
        };
        return {
          data: payload,
          count: filtered.length,
          as_of: legacy.asOf ?? "",
          cached: legacy.cached ?? false,
        };
      }
    },
    [filters, requestParams, search, serverView],
  );

  const queryKey = ["/api/ai-governance/inventory", requestParams];
  const query = useQuery({
    queryKey,
    queryFn: () => loadInventory(false),
    staleTime: 60_000,
    retry: false,
  });
  const inventory = useMemo(() => normalizeModelInventory(query.data), [query.data]);
  const rows = inventory.rows;
  const groups = useMemo(() => groupModelInventory(rows), [rows]);
  const selectedRow = selectedKey
    ? rows.find((row) => String(row.model_key) === selectedKey)
    : undefined;
  const hasFilters =
    Boolean(search) || FACET_FILTERS.some(([key]) => Boolean(filters[key]));

  const refresh = () =>
    queryClient.fetchQuery({
      queryKey,
      queryFn: () => loadInventory(true),
    });
  const selectRow = (row: ModelInventoryRow, event: MouseEvent<HTMLButtonElement>) => {
    setReturnFocus(event.currentTarget);
    updateParam("model", String(row.model_key ?? ""), false);
  };
  const closeDrawer = useCallback(() => {
    const next = new URLSearchParams(params);
    next.delete("model");
    setParams(next, { replace: true });
  }, [params, setParams]);

  const csvParams = new URLSearchParams();
  Object.entries(requestParams).forEach(([key, value]) => {
    if (!["cursor", "limit"].includes(key)) csvParams.set(key, String(value));
  });
  csvParams.set("format", "csv");
  const csvHref = `/api/ai-governance/inventory?${csvParams.toString()}`;

  return (
    <>
      <Card>
        <SectionTitle
          title="Model inventory"
          subtitle="Customer-managed and explicitly risky resources first; system models and raw evidence stay one action away."
          right={
            <AsOf
              asOf={inventory.asOf}
              cached={inventory.cached}
              onRefresh={refresh}
              refreshing={query.isFetching}
            />
          }
        />
        {query.isPending ? (
          <Skeleton rows={6} />
        ) : query.isError ? (
          <ErrorState error={query.error} />
        ) : inventory.summary.total === 0 ? (
          inventory.sourceHealth.some((item) => item.status !== "healthy") ? (
            <CapabilityNotice
              title="AI inventory evidence is unavailable"
              description={
                inventory.sourceHealth.map((item) => item.notes).filter(Boolean).join(" ") ||
                "Run the scheduled AI catalog sync and verify source permissions."
              }
            />
          ) : (
            <EmptyState
              positive={false}
              message="No AI inventory rows yet — run the scheduled ai-catalog sync job to populate this register."
            />
          )
        ) : (
          <>
            <SourceHealthBanner
              health={inventory.sourceHealth}
              rows={rows}
              truncated={inventory.truncated}
              total={inventory.total}
            />
            <div className="mb-4 grid grid-cols-2 gap-2 lg:grid-cols-4">
              <SummaryTile
                label="Observed inventory"
                value={inventory.summary.total}
                hint="current records across sources"
              />
              <SummaryTile
                label="Customer-managed"
                value={inventory.summary.customer_managed}
                hint="included by default"
              />
              <SummaryTile
                label="Needs attention"
                value={inventory.summary.needs_attention}
                hint="explicit evidence only"
              />
              <SummaryTile
                label="System catalog"
                value={inventory.summary.system}
                hint="collapsed unless risky"
              />
            </div>

            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div
                className="inline-flex rounded-lg border border-grid bg-page/30 p-0.5"
                aria-label="Inventory presentation"
              >
                {(["explorer", "audit"] as InventoryView[]).map((item) => (
                  <button
                    key={item}
                    type="button"
                    aria-pressed={presentation === item}
                    onClick={() =>
                      updateParam("inventoryView", item === "audit" ? "audit" : "", false)
                    }
                    className={`rounded-md px-2.5 py-1 text-xs font-medium ${
                      presentation === item
                        ? "bg-surface text-ink shadow-sm"
                        : "text-muted"
                    }`}
                  >
                    {item === "explorer" ? "Explore" : "Raw audit"}
                  </button>
                ))}
              </div>
              <label className="inline-flex items-center gap-2 text-xs text-ink-2">
                <input
                  type="checkbox"
                  checked={includeSystem}
                  onChange={(event) =>
                    updateParam("system", event.target.checked ? "1" : "")
                  }
                  className="accent-accent"
                />
                Include all Databricks system models
                {inventory.summary.system > 0 && (
                  <span className="text-muted">({inventory.summary.system} total)</span>
                )}
              </label>
            </div>

            <div className="mb-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
              <label
                htmlFor={searchId}
                className="flex items-center gap-2 rounded-lg border border-grid bg-page/40 px-2.5 py-2 text-xs focus-within:border-accent sm:col-span-2"
              >
                <Search className="h-3.5 w-3.5 shrink-0 text-muted" />
                <span className="sr-only">Search model inventory</span>
                <input
                  id={searchId}
                  type="search"
                  value={search}
                  onChange={(event) => updateParam("q", event.target.value)}
                  placeholder="Search model, endpoint, owner…"
                  className="w-full bg-transparent text-ink outline-none placeholder:text-muted"
                />
              </label>
              {FACET_FILTERS.map(([key, label]) => (
                <label key={key} className="text-[10px] font-medium text-muted">
                  <span className="sr-only">{label}</span>
                  <select
                    aria-label={label}
                    value={filters[key]}
                    onChange={(event) => updateParam(key, event.target.value)}
                    className="h-full w-full rounded-lg border border-grid bg-page px-2.5 py-2 text-xs text-ink-2"
                  >
                    <option value="">All {label.toLowerCase()}</option>
                    {facetOptions(inventory.facets, key).map((item) => (
                      <option key={item.value} value={item.value}>
                        {key === "source"
                          ? sourceLabel(item.value)
                          : item.value.replaceAll("_", " ")}{" "}
                        ({item.count})
                      </option>
                    ))}
                  </select>
                </label>
              ))}
              {hasFilters && (
                <button
                  type="button"
                  onClick={() => {
                    const next = new URLSearchParams(params);
                    next.delete("q");
                    next.delete("inventoryCursor");
                    FACET_FILTERS.forEach(([key]) => next.delete(key));
                    setParams(next, { replace: true });
                  }}
                  className="rounded-lg border border-grid px-2.5 py-2 text-xs font-medium text-ink-2 hover:bg-hairline"
                >
                  Clear filters
                </button>
              )}
            </div>

            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <p className="text-[11px] text-muted" aria-live="polite">
                {inventory.total} matching entities · {groups.length} groups on this page
              </p>
              {!inventory.legacyFallback && (
                <a
                  href={csvHref}
                  download
                  className="inline-flex items-center gap-1.5 rounded-lg border border-grid px-2.5 py-1.5 text-xs font-medium text-ink-2 hover:bg-hairline"
                >
                  <Download className="h-3.5 w-3.5" />
                  Export complete CSV
                </a>
              )}
            </div>

            {presentation === "audit" ? (
              <>
                <p className="mb-2 text-[11px] text-muted">
                  Server-filtered audit rows. The CSV export includes every matching row,
                  independent of this page.
                </p>
                {rows.length > 0 ? (
                  <DataTable
                    rows={rows}
                    maxRows={Number.MAX_SAFE_INTEGER}
                    searchable={false}
                    exportable={inventory.legacyFallback}
                    exportName="ai-model-inventory"
                    caption="Raw AI model inventory evidence"
                  />
                ) : (
                  <EmptyState positive={false} message="No audit rows match these filters." />
                )}
              </>
            ) : groups.length === 0 ? (
              <EmptyState
                positive={false}
                message="No models match these filters. Clear a filter or include system models."
              />
            ) : (
              <div className="grid gap-3 xl:grid-cols-2">
                {groups.map((group) => (
                  <GroupCard key={group.key} group={group} onSelect={selectRow} />
                ))}
              </div>
            )}

            {(inventory.nextCursor || cursor) && (
              <nav
                className="mt-4 flex items-center justify-between gap-2 border-t border-grid pt-3"
                aria-label="Model inventory pages"
              >
                <button
                  type="button"
                  disabled={!cursor}
                  onClick={() => updateParam("inventoryCursor", "", false)}
                  className="rounded-lg border border-grid px-3 py-1.5 text-xs font-medium text-ink-2 enabled:hover:bg-hairline disabled:opacity-40"
                >
                  First page
                </button>
                {inventory.nextCursor && (
                  <button
                    type="button"
                    onClick={() =>
                      updateParam("inventoryCursor", inventory.nextCursor ?? "", false)
                    }
                    className="rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-white hover:opacity-90"
                  >
                    Next page
                  </button>
                )}
              </nav>
            )}
          </>
        )}
      </Card>
      {selectedKey && (
        <ModelDetailDrawer
          modelKey={selectedKey}
          fallbackRow={selectedRow}
          returnFocus={returnFocus}
          onClose={closeDrawer}
        />
      )}
    </>
  );
}

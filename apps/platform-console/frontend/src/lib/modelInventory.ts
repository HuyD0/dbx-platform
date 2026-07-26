import type { Envelope, Row, SourceHealth } from "./types";

export interface ModelInventoryRow extends Row {
  source?: string | null;
  model_key?: string | null;
  provider?: string | null;
  model_name?: string | null;
  model_version?: string | null;
  entity_type?: string | null;
  endpoint_name?: string | null;
  status?: string | null;
  environment?: string | null;
  owner?: string | null;
  region?: string | null;
  resource_group?: string | null;
  resource_id?: string | null;
  key_auth_enabled?: boolean | string | number | null;
  usage_tracking?: boolean | string | number | null;
  details_json?: string | Row | null;
  first_seen_at?: string | null;
  last_seen_at?: string | null;
  ownership?: "customer_managed" | "system" | string;
  needs_attention?: boolean;
  risk?: "attention" | "clear" | string | null;
  risk_reasons?: string[];
  exposure?: string[];
  access_count?: number;
  tags?: Record<string, string>;
  group_key?: string | null;
  group_label?: string | null;
}

export interface InventoryFacet {
  value: string;
  count: number;
}

export interface InventorySummary {
  total: number;
  customer_managed: number;
  system: number;
  needs_attention: number;
  key_auth_exposed: number;
  groups_on_page: number;
}

export interface InventorySourceHealth extends SourceHealth {
  source_key?: string;
  row_count?: number;
  checked_at?: string | null;
  last_success_at?: string | null;
  truncated?: boolean;
}

export interface ModelInventoryPayload {
  items?: ModelInventoryRow[];
  rows?: ModelInventoryRow[];
  total?: number | null;
  count?: number | null;
  next_cursor?: string | null;
  truncated?: boolean;
  source_health?: InventorySourceHealth[];
  facets?: Record<string, InventoryFacet[]>;
  summary?: InventorySummary;
  legacy_fallback?: boolean;
}

export type ModelInventoryResponse =
  | ModelInventoryRow[]
  | (Partial<Envelope<ModelInventoryRow[] | ModelInventoryPayload>> & {
      source_health?: SourceHealth[];
      total?: number | null;
      next_cursor?: string | null;
      truncated?: boolean;
    });

export interface NormalizedModelInventory {
  rows: ModelInventoryRow[];
  total: number;
  asOf?: string;
  cached?: boolean;
  sourceHealth: InventorySourceHealth[];
  truncated: boolean;
  nextCursor?: string | null;
  facets: Record<string, InventoryFacet[]>;
  summary: InventorySummary;
  legacyFallback: boolean;
}

const EMPTY_SUMMARY: InventorySummary = {
  total: 0,
  customer_managed: 0,
  system: 0,
  needs_attention: 0,
  key_auth_exposed: 0,
  groups_on_page: 0,
};

export function normalizeModelInventory(
  response: ModelInventoryResponse | undefined,
): NormalizedModelInventory {
  if (!response) {
    return {
      rows: [],
      total: 0,
      sourceHealth: [],
      truncated: false,
      facets: {},
      summary: EMPTY_SUMMARY,
      legacyFallback: false,
    };
  }
  if (Array.isArray(response)) {
    return {
      rows: response,
      total: response.length,
      sourceHealth: [],
      truncated: false,
      facets: {},
      summary: {
        ...EMPTY_SUMMARY,
        total: response.length,
        customer_managed: response.filter((row) => !isDatabricksSystemModel(row)).length,
        system: response.filter(isDatabricksSystemModel).length,
        needs_attention: response.filter((row) => inventoryIssues(row).length > 0).length,
        key_auth_exposed: response.filter(
          (row) => booleanValue(row.key_auth_enabled) === true,
        ).length,
      },
      legacyFallback: true,
    };
  }

  const payload = response.data;
  const rows = Array.isArray(payload)
    ? payload
    : Array.isArray(payload?.items)
      ? payload.items
      : Array.isArray(payload?.rows)
        ? payload.rows
        : [];
  const payloadTotal = Array.isArray(payload)
    ? undefined
    : payload?.total ?? payload?.count;
  const total = Number(response.total ?? payloadTotal ?? response.count ?? rows.length);
  const nextCursor =
    response.next_cursor ?? (Array.isArray(payload) ? undefined : payload?.next_cursor);
  const truncated =
    Boolean(response.truncated) ||
    (!Array.isArray(payload) && Boolean(payload?.truncated)) ||
    Boolean(nextCursor) ||
    total > rows.length;
  const explicitHealth = [
    ...(response.source_health ?? []),
    ...(!Array.isArray(payload) ? (payload?.source_health ?? []) : []),
  ];
  const singularHealth = response.source_status
    ? [
        {
          source: response.source_status.source ?? "AI model catalog",
          status: response.source_status.status,
          notes: response.source_status.notes,
        } satisfies SourceHealth,
      ]
    : [];

  return {
    rows,
    total: Number.isFinite(total) ? total : rows.length,
    asOf: response.as_of,
    cached: response.cached,
    sourceHealth: explicitHealth.length > 0 ? explicitHealth : singularHealth,
    truncated,
    nextCursor,
    facets: Array.isArray(payload) ? {} : (payload?.facets ?? {}),
    summary: Array.isArray(payload)
      ? {
          ...EMPTY_SUMMARY,
          total: rows.length,
          customer_managed: rows.filter((row) => !isDatabricksSystemModel(row)).length,
          system: rows.filter(isDatabricksSystemModel).length,
          needs_attention: rows.filter((row) => inventoryIssues(row).length > 0).length,
          key_auth_exposed: rows.filter(
            (row) => booleanValue(row.key_auth_enabled) === true,
          ).length,
        }
      : (payload?.summary ?? {
          ...EMPTY_SUMMARY,
          total,
          customer_managed: rows.filter((row) => !isDatabricksSystemModel(row)).length,
          system: rows.filter(isDatabricksSystemModel).length,
          needs_attention: rows.filter((row) => inventoryIssues(row).length > 0).length,
          key_auth_exposed: rows.filter(
            (row) => booleanValue(row.key_auth_enabled) === true,
          ).length,
        }),
    legacyFallback: Array.isArray(payload) || Boolean(payload?.legacy_fallback),
  };
}

export function parseModelDetails(row: ModelInventoryRow): Row {
  if (row.details_json && typeof row.details_json === "object") {
    return row.details_json;
  }
  if (!row.details_json) return {};
  try {
    const parsed = JSON.parse(String(row.details_json)) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Row)
      : {};
  } catch {
    return {};
  }
}

function booleanValue(value: unknown): boolean | null {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  if (typeof value !== "string") return null;
  const normalized = value.trim().toLowerCase();
  if (["true", "yes", "1", "on", "enabled"].includes(normalized)) return true;
  if (["false", "no", "0", "off", "disabled"].includes(normalized)) return false;
  return null;
}

function detailValue(details: Row, ...keys: string[]): unknown {
  const sources = [
    details,
    details.tags,
    details.account_tags,
    details.governance,
  ].filter((value): value is Row => Boolean(value) && typeof value === "object");
  const requested = new Set(keys.map((key) => key.toLowerCase()));
  for (const source of sources) {
    for (const [key, value] of Object.entries(source)) {
      if (requested.has(key.toLowerCase())) return value;
    }
  }
  return undefined;
}

export function inventoryIssues(row: ModelInventoryRow): string[] {
  if (Array.isArray(row.risk_reasons) && row.risk_reasons.length > 0) {
    return Array.from(new Set(row.risk_reasons.map(String)));
  }
  const issues: string[] = [];
  const details = parseModelDetails(row);
  if (booleanValue(row.key_auth_enabled) === true) issues.push("Key auth enabled");
  const status = String(row.status ?? "").trim();
  if (
    status &&
    ["failed", "error", "unavailable", "degraded", "not ready"].some((value) =>
      status.toLowerCase().includes(value),
    )
  ) {
    issues.push(`Status: ${status}`);
  }
  const zdr = booleanValue(
    detailValue(
      details,
      "zdr",
      "zdr_enabled",
      "zero_data_retention",
      "zero_data_retention_enabled",
    ),
  );
  if (zdr === false) issues.push("ZDR disabled");
  const contentSafety = booleanValue(
    detailValue(details, "content_safety", "content_safety_enabled", "rai_policy_enabled"),
  );
  if (contentSafety === false) issues.push("Content safety disabled");
  const explicitRisk = String(row.risk ?? row.severity ?? "").toLowerCase();
  if (["critical", "high", "serious", "warning", "medium"].includes(explicitRisk)) {
    issues.push(`${explicitRisk} risk`);
  }
  return Array.from(new Set(issues));
}

export function isDatabricksSystemModel(row: ModelInventoryRow): boolean {
  if (row.ownership === "system") return true;
  if (row.ownership === "customer_managed") return false;
  if (row.source !== "databricks_uc") return false;
  const identifier = `${row.model_key ?? ""} ${row.model_name ?? ""}`.toLowerCase();
  return (
    identifier.includes("uc:system.ai.") ||
    identifier.includes("system.ai.") ||
    identifier.includes("`system`.`ai`")
  );
}

export function modelDisplayName(row: ModelInventoryRow): string {
  return String(
    row.model_name ||
      row.endpoint_name ||
      row.resource_id ||
      row.model_key ||
      "Unnamed model",
  );
}

export function sourceLabel(source: unknown): string {
  const value = String(source ?? "");
  if (value === "databricks_uc") return "Unity Catalog";
  if (value === "databricks_serving") return "Model Serving";
  if (value === "azure_openai") return "Azure AI";
  return value ? value.replaceAll("_", " ") : "Unknown source";
}

export function accessLookupKey(row: ModelInventoryRow): string {
  const key = String(row.model_key ?? "");
  if (row.source === "databricks_serving") return key.split("/")[0] ?? key;
  if (row.source === "azure_openai") return key.split("/deployments/")[0] ?? key;
  return key;
}

export interface ModelInventoryGroup {
  key: string;
  source: string;
  label: string;
  rows: ModelInventoryRow[];
  issueCount: number;
}

export function groupModelInventory(rows: ModelInventoryRow[]): ModelInventoryGroup[] {
  const groups = new Map<string, ModelInventoryGroup>();
  for (const row of rows) {
    const source = String(row.source ?? "unknown");
    let identity = String(
      row.group_key ?? row.model_key ?? row.resource_id ?? modelDisplayName(row),
    );
    let label = String(row.group_label ?? modelDisplayName(row));
    if (!row.group_key && source === "azure_openai") {
      const resourceId = String(row.resource_id ?? "").toLowerCase();
      identity = resourceId.split("/deployments/")[0] || String(row.endpoint_name ?? identity);
      label = String(row.endpoint_name || identity.split("/").at(-1) || modelDisplayName(row));
      if (identity.includes("/")) label = String(row.endpoint_name || identity.split("/").at(-1));
    } else if (!row.group_key && source === "databricks_serving") {
      identity = String(row.endpoint_name || String(row.model_key ?? "").split("/")[0] || identity);
      label = String(row.endpoint_name || identity);
    }
    const key = row.group_key ? String(row.group_key) : `${source}:${identity}`;
    const existing = groups.get(key);
    if (existing) {
      existing.rows.push(row);
      existing.issueCount += inventoryIssues(row).length;
    } else {
      groups.set(key, {
        key,
        source,
        label,
        rows: [row],
        issueCount: inventoryIssues(row).length,
      });
    }
  }
  return Array.from(groups.values()).sort(
    (left, right) =>
      Number(right.issueCount > 0) - Number(left.issueCount > 0) ||
      sourceLabel(left.source).localeCompare(sourceLabel(right.source)) ||
      left.label.localeCompare(right.label, undefined, { sensitivity: "base", numeric: true }),
  );
}

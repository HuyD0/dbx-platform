export type ApplicationWindow = 7 | 30 | 90;

export type AttributionMethod =
  | "DIRECT_METADATA"
  | "DIRECT_TAG"
  | "DIRECT_RESOURCE"
  | "CONFLICT"
  | "SHARED_UNALLOCATED"
  | "UNATTRIBUTED";

export type TagAlignmentStatus = "matched" | "missing" | "conflict";

export interface ApplicationIdentity {
  application_key: string;
  display_name: string;
  environments: string[];
  sources: string[];
  last_evidence_at?: string | null;
}

/**
 * A ledger is intentionally a single currency and pricing basis. Consumers
 * must render ledgers independently; adding them together is not meaningful.
 */
export interface CostLedger {
  id: string;
  source: string;
  title: string;
  amount: number;
  currency: string;
  pricing_basis: string;
  attributed_cost: number;
  unallocated_cost: number;
  coverage_start?: string | null;
  coverage_end?: string | null;
  freshness?: string | null;
  scope?: string[] | string | null;
  status?: string;
  trusted?: boolean;
  trend_pct?: number | null;
}

export interface ApplicationCoverage {
  source: string;
  status: string;
  currency: string;
  pricing_basis: string;
  attributed_rows: number;
  total_rows: number;
  attributed_cost: number;
  total_cost: number;
  coverage_pct: number | null;
}

export interface ApplicationSummary {
  application_key: string;
  display_name: string;
  environments: string[];
  sources: string[];
  ledgers: CostLedger[];
  trend_pct: number | null;
  tag_health: {
    status: string;
    matched: number;
    missing: number;
    conflicts: number;
  };
  coverage_pct: number | null;
  last_evidence_at?: string | null;
}

export interface ApplicationFacets {
  environments: string[];
  sources: string[];
}

export interface ApplicationPortfolio {
  applications: ApplicationSummary[];
  facets: ApplicationFacets;
  next_cursor: string | null;
}

export interface ApplicationPeriod {
  window: string;
  days: number;
  start: string | null;
  end: string | null;
}

export interface ApplicationTrendPoint {
  usage_date: string;
  ledger_id: string;
  cost: number;
  currency: string;
  pricing_basis: string;
}

export interface ApplicationCostDriver {
  ledger_id: string;
  source: string;
  dimension: string;
  name: string;
  resource_type?: string | null;
  resource_id?: string | null;
  service?: string | null;
  workload?: string | null;
  cost: number;
  currency: string;
  pricing_basis: string;
  attribution_method: AttributionMethod;
}

export interface TagAlignment {
  source: string;
  resource_id?: string | null;
  resource_name?: string | null;
  tag_key?: string | null;
  raw_value?: string | null;
  normalized_value?: string | null;
  observed_cost?: number | null;
  evidence_id?: string | null;
  scope?: string | null;
  freshness?: string | null;
  status: TagAlignmentStatus;
}

export interface ApplicationUnallocatedCost {
  source: string;
  reason: string;
  cost: number;
  currency: string;
  pricing_basis: string;
  row_count: number;
}

export interface ApplicationSourceHealth {
  source: string;
  status: "healthy" | "partial" | "degraded" | "stale" | "unavailable" | string;
  last_success_at?: string | null;
  evidence_at?: string | null;
  freshness?: string | null;
  scope?: string | null;
  subscription_id?: string | null;
  scope_filter?: string | null;
  coverage_start?: string | null;
  coverage_end?: string | null;
  notes?: string | null;
}

export interface ApplicationProfile {
  application: ApplicationIdentity;
  period: ApplicationPeriod;
  ledgers: CostLedger[];
  series: ApplicationTrendPoint[];
  drivers: ApplicationCostDriver[];
  tag_alignment: TagAlignment[];
  coverage: ApplicationCoverage[];
  unallocated: ApplicationUnallocatedCost[];
  source_health: ApplicationSourceHealth[];
}

export interface ApplicationEvidence {
  evidence_id: string;
  usage_date: string;
  source: string;
  environment: string;
  resource_type?: string | null;
  resource_name?: string | null;
  resource_id?: string | null;
  service?: string | null;
  workload?: string | null;
  application_key: string;
  raw_application?: string | null;
  cost: number;
  cost_known?: boolean;
  inventory_only?: boolean;
  evidence_kind?: string | null;
  currency: string;
  pricing_basis: string;
  attribution_method: AttributionMethod;
  tag_key?: string | null;
  tags?: Record<string, string>;
  evidence_at: string;
  scope: string;
  conflict_values?: string[];
}

export interface ApplicationEvidencePage {
  items: ApplicationEvidence[];
  next_cursor: string | null;
}

export interface ApplicationEnvelope<T> {
  data: T;
  count: number | null;
  as_of: string;
  cached: boolean;
  source_status?: {
    status: string;
    source?: string;
    notes?: string;
  };
}

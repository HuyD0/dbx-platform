export type Row = Record<string, unknown>;

export interface Envelope<T> {
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

export interface ApiErrorPayload {
  error: string;
  message: string;
  hint?: string;
}

export class ApiError extends Error {
  code: string;
  hint?: string;
  status: number;

  constructor(status: number, payload: ApiErrorPayload) {
    super(payload.message);
    this.status = status;
    this.code = payload.error;
    this.hint = payload.hint;
  }
}

export interface OverviewSection<T> {
  data?: T;
  error?: ApiErrorPayload;
}

export interface OverviewData {
  findings: OverviewSection<{
    run_ts: string | null;
    total: number;
    by_area: Record<string, number>;
    by_action: Record<string, number>;
  }>;
  spend: OverviewSection<Row[]>;
  digest: OverviewSection<{ latest_run_ts: string | null }>;
}

export interface PlanResponse {
  plan_id: string;
  action: string;
  expires_at: number | string;
  items: Row[];
  summary: Record<string, number>;
  confirm_phrase: string;
  actions_enabled: boolean;
  plan_hash?: string;
  status?: string;
  risk?: string;
  impact?: Row;
  rollback?: Row | string;
  verification?: Row | string;
  approver_required?: boolean;
}

export interface ApplyResponse {
  plan_id: string;
  action: string;
  applied?: string[];
  status?: string;
  execution_id?: string;
}

export interface JobInfo {
  job_id: number;
  name: string;
}

export interface RunAllResponse {
  runs: (JobInfo & { run_id: number })[];
  failed: (JobInfo & { error: string })[];
  count: number;
}

export interface RunInfo {
  run_id: number;
  state: string;
  result: string;
  started_ms: number;
  duration_ms: number | null;
}

export interface Proposal {
  kind: "action" | "job";
  action?: string;
  count?: number;
  job_id?: number;
  name?: string;
  all?: boolean;
}

export interface ChatResponse {
  message: string;
  proposals: Proposal[];
  endpoint: string;
}

export interface DashboardInfo {
  name: string;
  url: string;
  embed_url: string;
}

export interface HealthResponse {
  status: string;
  version: string;
  build?: { sha: string; built_at: string } | null;
  actions_enabled: boolean;
  environment?: string;
}

export interface SourceHealth {
  source: string;
  status: "healthy" | "degraded" | "unavailable" | "unknown" | string;
  freshness?: string | null;
  retention_days?: number | null;
  notes?: string | null;
}

export interface PillarOutcome {
  status?: string;
  score?: number | null;
  open_findings?: number;
  critical_findings?: number;
  value?: string | number;
  trend?: number | null;
  summary?: string;
}

export interface MissionControlData {
  scope?: {
    workspace?: string;
    workspace_name?: string;
    environment?: string;
    region?: string;
  };
  outcomes?: Record<string, PillarOutcome>;
  pending_approvals?: number;
  decisions?: Row[];
  changes?: Row[];
  data_health?: SourceHealth[];
  findings?: OverviewData["findings"];
  spend?: OverviewData["spend"];
  digest?: OverviewData["digest"];
}

export interface ActionRequest {
  id?: string;
  plan_id?: string;
  action?: string;
  action_type?: string;
  status?: string;
  risk?: string;
  proposer?: string;
  approver?: string;
  created_at?: string;
  expires_at?: string;
  plan_hash?: string;
  target_count?: number;
  [key: string]: unknown;
}

export interface RuntimeState {
  desired_state?: "ON" | "SLEEPING" | string;
  current_state?: "ON" | "SLEEPING" | "TRANSITIONING" | string;
  updated_at?: string | null;
  active_operation?: string | null;
  operation_status?: string | null;
  wake_instructions?: string | null;
}

export interface LlmCostTotal {
  currency: string;
  cost: number;
  basis: string;
  previous_period_cost?: number;
  period_delta_pct?: number | null;
  comparison_from?: string;
  comparison_to?: string;
}

export interface LlmCostSummary {
  period: { days: number; from: string; to: string };
  totals: LlmCostTotal[];
  requests: number;
  input_tokens: number;
  output_tokens: number;
  cached_tokens?: number;
  reasoning_tokens?: number;
  cost_per_request?: number | null;
  cost_per_million_tokens?: number | null;
  forecast?: {
    month_end?: number | null;
    lower?: number | null;
    upper?: number | null;
    currency?: string;
    basis?: string;
  };
  coverage?: SourceHealth[];
}

export interface LlmCostPoint {
  usage_date: string;
  provider?: string;
  model?: string;
  cost: number;
  currency: string;
  cost_basis: string;
  requests?: number;
  input_tokens?: number;
  output_tokens?: number;
  [key: string]: unknown;
}

export interface LlmBreakdown {
  dimension: string;
  key: string;
  cost: number;
  currency: string;
  cost_basis: string;
  requests?: number;
  input_tokens?: number;
  output_tokens?: number;
  [key: string]: unknown;
}

export interface LlmEfficiency {
  metrics?: Row;
  recommendations?: Row[];
  [key: string]: unknown;
}

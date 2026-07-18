export type Row = Record<string, unknown>;

export interface Envelope<T> {
  data: T;
  count: number | null;
  as_of: string;
  cached: boolean;
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
  expires_at: number;
  items: Row[];
  summary: Record<string, number>;
  confirm_phrase: string;
  actions_enabled: boolean;
}

export interface ApplyResponse {
  plan_id: string;
  action: string;
  applied: string[];
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
}

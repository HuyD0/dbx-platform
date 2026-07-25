export type Row = Record<string, unknown>;
export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonObject | JsonValue[];
export type JsonObject = { [key: string]: JsonValue };

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

export interface GatewayTelemetryRow {
  usage_date: string;
  endpoint_name?: string | null;
  app?: string | null;
  requests?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  p95_latency_ms?: number | null;
  source?: string | null;
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
  schedule_status?: "PAUSED" | "UNPAUSED" | "UNSCHEDULED" | string;
  schedule_type?: "CRON" | "MANUAL_ONLY" | string;
}

export interface RunInfo {
  run_id: number;
  state: string;
  result: string;
  state_message?: string;
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

export interface AssistantCitation {
  citation_id: string;
  tool: string;
  source: string;
  observed_at: string;
  resource?: string;
  finding_id?: string;
}

export type AgentExecutionCategory =
  | "foundry_agent"
  | "databricks_retrieval"
  | "llm_synthesis";

export interface AgentExecutionStage {
  id: string;
  label: string;
  category: AgentExecutionCategory;
  start_ms: number;
  duration_ms: number | null;
  detail?: string;
}

/** Optional server-observed assistant timing. Nulls and the unavailable source
 * are intentionally first-class so the console never fabricates latency. */
export interface AgentExecutionTrace {
  total_ms: number | null;
  ttft_ms: number | null;
  tpot_ms: number | null;
  timing_source: "server" | "unavailable";
  stages: AgentExecutionStage[];
}

export interface ComplianceMetric {
  id: "zdr" | "content_safety" | "access_control" | "audit_logging" | "rate_limit_headroom";
  label: string;
  value_pct: number | null;
  compliant_resources: number;
  evaluated_resources: number;
  total_resources: number;
  evidence_note: string;
}

export interface ZdrAlert {
  resource_id: string;
  resource_name: string;
  scope: string;
  provider: string;
  status: "disabled";
  remediation: string;
}

export interface AiCompliancePosture {
  metrics: ComplianceMetric[];
  zdr_alerts: ZdrAlert[];
  unverified_zdr_resources: number;
  evaluated_resources: number;
}

export interface ChatResponse {
  message: string;
  proposals: Proposal[];
  citations?: AssistantCitation[];
  endpoint: string;
  execution_trace?: AgentExecutionTrace;
}

export interface DashboardInfo {
  name: string;
  url: string;
  embed_url: string;
}

export interface WorkspaceCapability {
  id: string;
  label: string;
  description: string;
  enabled: boolean;
}

export interface WorkspaceAccess {
  workspace_id: string | null;
  name: string;
  environment: string;
  relationship: "platform_admin" | "workspace_user" | string;
  roles: string[];
  capabilities: WorkspaceCapability[];
  management_mode: "governed_approval" | "viewer_safe" | string;
}

export interface WorkspaceAccessResponse {
  actor: {
    actor_id: string;
    email: string | null;
    roles: string[];
    view: "platform_admin" | "workspace_user" | string;
  };
  workspaces: WorkspaceAccess[];
  source_status: SourceHealth;
}

export interface HealthResponse {
  status: string;
  version: string;
  build?: { sha: string; built_at: string } | null;
  actions_enabled: boolean;
  environment?: string;
  workspace_id?: string | null;
}

export interface AppContext {
  workspace_name: string;
  workspace_id: string;
  environment: string;
  roles: string[];
  actions_enabled: boolean;
}

export interface CostSource {
  id: "databricks_list" | "azure_actual" | "ai_ledger" | string;
  title: string;
  status: "healthy" | "no_data" | "not_configured" | "never_run" | "stale" | "unavailable";
  amount?: number | null;
  currency?: string | null;
  cost_basis?: string | null;
  coverage_start?: string | null;
  coverage_end?: string | null;
  freshness?: string | null;
  notes: string;
  series: Array<{ date?: string | null; amount: number; currency?: string | null }>;
  cached?: boolean;
  as_of?: string;
  job?: {
    job_id?: number;
    job_name?: string;
    schedule_status?: string;
    last_run_result?: string;
    last_run_started_at?: string | null;
  };
  refresh_action?: {
    action_type: "run-job";
    job_id: number;
    job_name: string;
  } | null;
}

export interface CostOverviewData {
  scope: {
    workspace_name: string;
    workspace_id: string;
    environment: string;
  };
  days: number;
  sources: CostSource[];
}

export interface SourceHealth {
  source: string;
  status: "healthy" | "degraded" | "unavailable" | "unknown" | string;
  freshness?: string | null;
  retention_days?: number | null;
  notes?: string | null;
}

export interface CostTotal {
  currency: string;
  cost: number;
  previous_period_cost: number;
  period_delta_pct: number | null;
  cost_basis: "AZURE_ACTUAL" | string;
}

export interface CostPoint extends Row {
  usage_date: string;
  category: string;
  currency: string;
  cost: number;
  cost_basis: "AZURE_ACTUAL" | string;
}

export interface CostBreakdown extends Row {
  category?: string;
  component?: string;
  owner?: string;
  cost: number;
  currency: string;
  cost_basis: string;
  share_pct: number;
}

export interface CostMover extends CostBreakdown {
  category: string;
  previous_cost: number;
  change: number;
  change_pct: number | null;
}

export interface CostAnomaly extends Row {
  id: string;
  signal: string;
  day: string;
  category: string;
  currency: string;
  cost_basis: string;
  cost: number;
  baseline: number;
  change_pct: number;
  severity: string;
  reason: string;
}

export type BillingAlignmentStatus =
  | "aligned"
  | "variances_found"
  | "delayed_source"
  | "unavailable";

export interface BillingAlignmentTotal {
  cost: number;
  currency: string;
  cost_basis: "AZURE_ACTUAL" | "DATABRICKS_LIST";
}

export interface BillingAlignmentSummary {
  status: BillingAlignmentStatus;
  variance_count: number;
  unmatched_count: number;
  latest_azure_date: string | null;
  latest_databricks_date: string | null;
  azure_lag_days: number | null;
  databricks_lag_days: number | null;
  azure_totals: BillingAlignmentTotal[];
  databricks_totals: BillingAlignmentTotal[];
  largest_pattern_variance: {
    usage_date: string;
    sku_family: string;
    delta_pct_points: number;
  } | null;
  money_comparable: boolean;
  notes: string;
}

export interface BillingAlignmentRow extends Row {
  usage_date: string;
  sku_family: string;
  databricks_list_usd: number | null;
  azure_billed_cost: number | null;
  azure_currency: string;
  comparison_status: string;
  classifications: string[];
  evaluated: boolean;
  money_comparable: boolean;
  pattern_delta_pct_points: number | null;
  azure_window_share_pct?: number;
  databricks_window_share_pct?: number;
  variance: number | null;
  variance_pct: number | null;
  databricks_skus?: string[] | null;
  azure_meters?: string[] | null;
}

export interface CostOverview {
  scope: {
    label: string;
    workspace_name?: string;
    workspace_id: string;
    environment: string;
    resource_groups: string[];
  };
  period: { days: number; from: string | null; to: string | null };
  totals: CostTotal[];
  components: CostBreakdown[];
  series: CostPoint[];
  categories: CostBreakdown[];
  ownership: CostBreakdown[];
  movers: CostMover[];
  anomalies: CostAnomaly[];
  databricks_list: {
    cost: number;
    currency: string;
    cost_basis: string;
    additive_to_total: false;
    rows: Row[];
    status: string;
    notes: string;
  };
  billing_alignment: BillingAlignmentSummary;
  data_health: SourceHealth[];
  source_cards: CostSource[];
}

export interface CostAnomalyDetail {
  anomaly: CostAnomaly;
  series: CostPoint[];
  mover?: CostMover | null;
  scope: CostOverview["scope"];
  databricks_list: CostOverview["databricks_list"];
  investigation: {
    checks: string[];
    safe_actions: string[];
  };
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

/** Risk values emitted by the action response adapter. */
export type ActionRisk = "low" | "medium" | "high";

export type ActionStatus =
  | "AWAITING_APPROVAL"
  | "APPROVED"
  | "EXECUTING"
  | "VERIFYING"
  | "SUCCEEDED"
  | "FAILED"
  | "ROLLED_BACK"
  | "REJECTED"
  | "EXPIRED"
  | "STALE";

export type EvidenceCoverageStatus =
  | "MATCHED"
  | "NO_MATCH"
  | "NO_TARGETS"
  | "UNAVAILABLE";

export interface DecisionEvidenceSummary {
  matched_count: number;
  pillars: string[];
  freshest_at: string | null;
  coverage_status: EvidenceCoverageStatus;
}

export interface DecisionQueueItem {
  action_id: string;
  action_type: string;
  status: ActionStatus;
  raw_status: ActionStatus;
  effective_status: ActionStatus;
  risk: ActionRisk;
  target_count: number;
  proposer_id: string;
  proposer_email: string | null;
  created_at: string;
  expires_at: string;
  can_approve: boolean;
  impact: JsonObject;
  evidence_summary: DecisionEvidenceSummary;
}

export interface DecisionQueue {
  evaluated_at: string;
  ranking: "risk-expiry-created-v1";
  active_count: number;
  expiring_soon_count: number;
  expired_count: number;
  items: DecisionQueueItem[];
}

export type EvidenceRelationship = "supports_action" | "same_target";

export interface ActionEvidenceItem {
  finding_id: string | null;
  check_name: string | null;
  match_type: EvidenceRelationship;
  pillar: string;
  severity: string;
  confidence: number | null;
  owner: string | null;
  reason: string | null;
  state: string;
  freshness_at: string | null;
  proposed_action_type: string | null;
  affected_resources: JsonObject[];
}

export interface ActionEvidenceCorrelation {
  items: ActionEvidenceItem[];
  total: number;
  truncated: boolean;
  coverage_status: EvidenceCoverageStatus;
}

export type ApprovalDecision = "APPROVED" | "REJECTED";

export interface ActionApproval {
  approval_id: string;
  action_id: string;
  plan_hash: string;
  decision: ApprovalDecision;
  approver_id: string;
  approver_email: string | null;
  approver_role: string;
  confirmation: string | null;
  decided_at: string;
}

export interface ActionEvent {
  event_id: string;
  action_id: string;
  event_type: string;
  from_status: ActionStatus | null;
  to_status: ActionStatus | null;
  actor_id: string | null;
  details: JsonObject;
  event_ts: string;
}

export type ActionTimelineStage =
  | "plan"
  | "approval"
  | "execution"
  | "verification"
  | "outcome";

export interface ActionTimelineItem {
  id: string;
  stage: ActionTimelineStage;
  label: string;
  timestamp?: string | null;
  actor?: string | null;
  status?: string | null;
  detail?: string | null;
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
  decision_queue?: DecisionQueue;
  decisions?: LegacyDecisionRow[];
  changes?: ActionRequest[];
  data_health?: SourceHealth[];
  findings?: OverviewData["findings"];
  spend?: OverviewData["spend"];
  digest?: OverviewData["digest"];
}

/** Unstructured rows are accepted only by the explicit legacy Mission Control adapter. */
export type LegacyDecisionRow = Row;

export interface ActionRequest {
  schema_version: number;
  action_id: string;
  action_type: string;
  workspace_id: string;
  environment: string;
  targets: JsonObject[];
  parameters: JsonObject;
  preconditions: JsonObject;
  before_state: JsonValue;
  after_state: JsonValue;
  impact: JsonObject;
  rollback: JsonObject;
  verification: JsonObject;
  risk: ActionRisk;
  proposer_id: string;
  proposer_email: string | null;
  created_at: string;
  expires_at: string;
  idempotency_key: string;
  confirm_phrase: string;
  plan_hash: string;
  status: ActionStatus;
  updated_at: string;
  terminal_reason: string | null;
  plan_id: string;
  action: string;
  items: JsonObject[];
  summary: JsonValue;
  actions_enabled: boolean;
  approver_required: boolean;
  raw_status: ActionStatus;
  effective_status: ActionStatus;
  evaluated_at: string;
  can_approve: boolean;
  expiry_guidance?: string | null;
  target_count?: number;
}

export interface ActionRequestDetail extends ActionRequest {
  evidence_correlation: ActionEvidenceCorrelation;
  approvals: ActionApproval[];
  events: ActionEvent[];
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
  unit_economics_scope?: "MODEL_SERVING" | string;
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
  cost: number | null;
  currency: string | null;
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

// --- AI Cost Planner (estimator) ---

export interface EstimatorPattern {
  pattern: string;
  label: string;
  description: string;
  example_prompt: string;
  defaults: Record<string, unknown>;
}

export interface EstimateLineItem {
  component: string;
  env: string;
  tier: string;
  scenario: string;
  label: string;
  quantity: number;
  unit: string;
  unit_price: number | null;
  currency: string | null;
  price_source: string | null;
  meter_name: string | null;
  snapshot_date: string | null;
  provenance: string | null;
  monthly_cost: number | null;
  formula: string;
  assumptions: string[];
  is_eval_tax: boolean;
  eval_group: string | null;
}

export interface RigorCurveEnv {
  total_fixed: number;
  total_slope_per_pct: number;
  eval_fixed: number;
  eval_slope_per_pct: number;
}

export interface TierScenarioEstimate {
  tier: string;
  scenario: string;
  rigor_pct: number;
  line_items: EstimateLineItem[];
  totals_by_env: Record<string, number>;
  run_cost_by_env: Record<string, number>;
  eval_tax_by_env: Record<string, number>;
  improvement_pipeline_by_env: Record<string, number>;
  missing_prices: string[];
  rigor_curve: { pinned: boolean; by_env: Record<string, RigorCurveEnv> };
}

export interface EstimateTier {
  label: string;
  description: string;
  rigor_locked: boolean;
  rigor_locked_reason: string;
  default_rigor_pct: number;
  scenarios: Record<string, TierScenarioEstimate>;
}

export interface EstimateMatrix {
  engine_version: string;
  rate_card_version: string;
  snapshot_date: string;
  requirements: Record<string, unknown>;
  rigor_pct: number;
  requirements_hash: string;
  blueprint: { title: string; body: string }[];
  tiers: Record<string, EstimateTier>;
}

export interface ExtractResponse {
  requirements: Record<string, unknown>;
  warnings: string[];
}

export interface PricingStatus {
  sources: Row[];
  snapshot_date?: string | null;
  coverage_findings: Row[];
  notes: string[];
  health: SourceHealth;
}

export interface SavedEstimateSummary {
  estimate_id: string;
  created_at: string;
  created_by?: string;
  title: string;
  pattern: string;
  monthly_requests: number;
  corpus_gb: number;
  requirements_json: string;
  requirements_hash: string;
  engine_version?: string;
  rate_card_version?: string;
  snapshot_date?: string;
  rigor_pct: number;
}

export interface SimilarEstimatesResponse {
  exact_match: SavedEstimateSummary | null;
  similar: SavedEstimateSummary[];
  bracket: { lo: number; hi: number };
}

export interface DeploymentLink {
  deployment_id: string;
  estimate_id: string;
  created_at: string;
  created_by?: string;
  tier: string;
  scenario: string;
  anchor_kind: string;
  anchor_value: string;
  monthly_projected_usd: number;
  currency: string;
  active: boolean;
}

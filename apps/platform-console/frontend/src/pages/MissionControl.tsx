import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  Bot,
  CheckCircle2,
  CircleDollarSign,
  Clock3,
  FileSearch,
  Gauge,
  ListChecks,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  UserRound,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ActionReviewDialog } from "../components/ActionReviewDialog";
import { PlanActionButton } from "../components/ActionPlanDialog";
import { DataTable } from "../components/DataTable";
import { MissionDecisionDetail } from "../components/MissionDecisionDetail";
import {
  AsOf,
  Badge,
  Card,
  DataHealthList,
  EmptyState,
  ErrorState,
  HealthDot,
  PageHeader,
  SectionTitle,
  Skeleton,
  statusTone,
} from "../components/ui";
import { apiGet, isUnavailable } from "../lib/api";
import { useAssistantPanel } from "../lib/assistant-panel";
import { useChat } from "../lib/chat";
import { timeAgo, usd } from "../lib/format";
import type {
  ActionRequest,
  DecisionQueueItem,
  Envelope,
  MissionControlData,
  OverviewData,
  PillarOutcome,
  Row,
  SourceHealth,
} from "../lib/types";

interface MissionResult {
  envelope: Envelope<MissionControlData>;
  compatibility: boolean;
}

interface RawMissionControl {
  scope?: MissionControlData["scope"];
  outcomes?: {
    open_findings?: number;
    by_pillar?: Record<string, number>;
    by_severity?: Record<string, number>;
    awaiting_approval?: number;
  };
  top_decisions?: Row[];
  runtime?: Row;
  data_health?: Record<string, unknown>;
}

const PILLARS = [
  {
    key: "cost",
    label: "Cost",
    href: "/cost",
    icon: CircleDollarSign,
    description: "Spend & efficiency",
  },
  {
    key: "security",
    label: "Security",
    href: "/security",
    icon: ShieldCheck,
    description: "Identity & policy",
  },
  {
    key: "risk",
    label: "Risk",
    href: "/security?tab=risk",
    icon: TriangleAlert,
    description: "Control posture",
  },
  {
    key: "performance",
    label: "Performance",
    href: "/performance",
    icon: Gauge,
    description: "SLO & reliability",
  },
] as const;

const ACTION_TITLES: Record<string, string> = {
  "stale-clusters": "Stop stale clusters",
  "orphaned-jobs": "Pause orphaned schedules",
  "token-revoke": "Revoke over-age PATs",
  "policy-sync": "Synchronize managed policies",
  "run-job": "Run an evidence job",
  "configure-budget": "Update a governed budget",
  "runtime.hibernate": "Hibernate resources",
  "runtime.wake": "Wake resources",
};

const LEGACY_ACTION_TITLES: Record<string, string> = {
  ...ACTION_TITLES,
};

const PLANNABLE_ACTIONS = new Set([
  "stale-clusters",
  "orphaned-jobs",
  "token-revoke",
  "policy-sync",
]);

function humanize(value: string): string {
  const words = value.replace(/[._-]+/g, " ").replace(/\s+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "Governed action";
}

function actionTitle(actionType: string): string {
  return ACTION_TITLES[actionType] ?? humanize(actionType);
}

function rowText(row: Row, ...keys: string[]): string | null {
  for (const key of keys) {
    const value = row[key];
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return null;
}

function affectedCount(row: Row): number {
  return Array.isArray(row.affected_resources) ? row.affected_resources.length : 0;
}

function sourceHealthFromRaw(raw: Record<string, unknown> | undefined): SourceHealth[] {
  return Object.entries(raw ?? {}).map(([source, value]) => {
    const detail =
      value && typeof value === "object" && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : null;
    return {
      source: source.replaceAll("_", " "),
      status: String(detail?.status ?? value ?? "unknown"),
      freshness:
        typeof detail?.freshness === "string"
          ? detail.freshness
          : typeof detail?.as_of === "string"
            ? detail.as_of
            : null,
      notes:
        typeof detail?.notes === "string"
          ? detail.notes
          : "Reported by the Mission Control repository",
    };
  });
}

export async function fetchMissionControl(refresh = false): Promise<MissionResult> {
  try {
    const response = await apiGet<Envelope<MissionControlData> | RawMissionControl>(
      `/api/mission-control${refresh ? "?refresh=true" : ""}`,
    );
    if ("data" in response) {
      return { envelope: response, compatibility: false };
    }
    const byPillar = Object.fromEntries(
      Object.entries(response.outcomes?.by_pillar ?? {}).map(([pillar, count]) => [
        pillar.toLowerCase(),
        Number(count),
      ]),
    );
    return {
      envelope: {
        data: {
          scope: response.scope,
          outcomes: Object.fromEntries(
            Object.entries(byPillar).map(([pillar, count]) => [
              pillar,
              {
                value: count,
                open_findings: count,
                status: Number(count) > 0 ? "attention" : "unknown",
              },
            ]),
          ),
          pending_approvals: response.outcomes?.awaiting_approval,
          decisions: response.top_decisions ?? [],
          data_health: sourceHealthFromRaw(response.data_health),
          findings: {
            data: {
              run_ts: null,
              total: response.outcomes?.open_findings ?? 0,
              by_area: byPillar,
              by_action: {},
            },
          },
        },
        count: null,
        as_of: "",
        cached: false,
      },
      compatibility: true,
    };
  } catch (error) {
    if (!isUnavailable(error)) throw error;
    const legacy = await apiGet<Envelope<OverviewData>>("/api/overview");
    return {
      envelope: {
        ...legacy,
        data: {
          findings: legacy.data.findings,
          spend: legacy.data.spend,
          digest: legacy.data.digest,
        },
      },
      compatibility: true,
    };
  }
}

function fallbackOutcome(
  key: string,
  data: MissionControlData,
  spendTotal: number,
): PillarOutcome {
  const byArea = data.findings?.data?.by_area ?? {};
  const areas: Record<string, string[]> = {
    cost: ["cost", "azure_cost", "forecast"],
    security: ["security"],
    risk: ["governance", "housekeeping", "ml"],
    performance: ["performance", "jobs", "sql"],
  };
  const findings = (areas[key] ?? []).reduce(
    (total, area) => total + Number(byArea[area] ?? 0),
    0,
  );
  if (key === "cost" && spendTotal > 0) {
    return {
      value: usd(spendTotal),
      status: findings > 0 ? "attention" : "unknown",
      open_findings: findings,
    };
  }
  return {
    value: findings,
    status: findings > 0 ? "attention" : "unknown",
    open_findings: findings,
  };
}

function fallbackDecisions(data: MissionControlData): Row[] {
  const byArea = data.findings?.data?.by_area ?? {};
  return Object.entries(byArea)
    .filter(([, count]) => Number(count) > 0)
    .sort((left, right) => Number(right[1]) - Number(left[1]))
    .slice(0, 3)
    .map(([area, count], index) => ({
      priority: index + 1,
      decision: `Review ${area.replaceAll("_", " ")} findings`,
      pillar: area,
      evidence: `${count} open finding${Number(count) === 1 ? "" : "s"}`,
      confidence: "reported",
      approval: "required for changes",
    }));
}

interface LegacyDecision {
  action: string | null;
  title: string;
  description: string;
  evidence: string;
  impact: string;
  pillar: string;
  severity: string;
}

function normalizeLegacyDecision(row: Row): LegacyDecision {
  const rawAction = rowText(row, "proposed_action_type", "action");
  const action = rawAction && PLANNABLE_ACTIONS.has(rawAction) ? rawAction : null;
  const pillar = humanize(rowText(row, "pillar", "area") ?? "risk");
  const check = rowText(row, "check_name", "check");
  const title =
    rowText(row, "decision", "title", "recommendation") ??
    (rawAction ? LEGACY_ACTION_TITLES[rawAction] ?? humanize(rawAction) : null) ??
    (check ? `Review ${humanize(check).toLowerCase()}` : `Review ${pillar.toLowerCase()} evidence`);
  const targets = affectedCount(row);
  const evidence =
    rowText(row, "evidence") ??
    (targets > 0
      ? `${targets} affected resource${targets === 1 ? "" : "s"}`
      : check
        ? humanize(check)
        : "Canonical finding");
  const financialImpact = Number(row.financial_impact_usd ?? 0);
  const severity = humanize(rowText(row, "severity") ?? "reported");
  const impact =
    Number.isFinite(financialImpact) && financialImpact > 0
      ? `${usd(financialImpact)} exposure`
      : rowText(row, "slo_impact") ?? `${severity} severity`;
  return {
    action,
    title,
    description:
      rowText(row, "reason", "description", "summary") ??
      `This ${pillar.toLowerCase()} finding needs human review.`,
    evidence,
    impact,
    pillar,
    severity,
  };
}

function impactSummary(item: DecisionQueueItem): string {
  const summary = rowText(item.impact, "summary", "description", "impact");
  if (summary) return summary;
  const changed = Number(item.impact.changed_resource_count ?? item.impact.target_count ?? NaN);
  if (Number.isFinite(changed)) {
    return `${changed} resource${changed === 1 ? "" : "s"} expected to change`;
  }
  return "Impact recorded in exact plan";
}

/** Adapt strict governed outcomes only at the generic legacy table boundary. */
function governedOutcomeRows(actions: ActionRequest[]): Row[] {
  return actions.map((action) => ({
    action_type: action.action_type,
    effective_status: action.effective_status,
    risk: action.risk,
    target_count: action.target_count ?? action.targets.length,
    proposer_email: action.proposer_email,
    updated_at: action.updated_at,
    plan_hash: action.plan_hash,
  }));
}

function useWideLayout(): boolean {
  const [wide, setWide] = useState(
    () => typeof window !== "undefined" && (window.matchMedia?.("(min-width: 1280px)").matches ?? false),
  );
  useEffect(() => {
    const media = window.matchMedia?.("(min-width: 1280px)");
    if (!media) return;
    const update = () => setWide(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return wide;
}

function useServerClock(evaluatedAt: string | undefined): number {
  const [clock, setClock] = useState(() => Date.now());
  const anchor = useRef({ local: Date.now(), server: Date.now() });
  useEffect(() => {
    const parsed = evaluatedAt ? Date.parse(evaluatedAt) : NaN;
    anchor.current = {
      local: Date.now(),
      server: Number.isFinite(parsed) ? parsed : Date.now(),
    };
    const update = () => {
      const elapsed = Date.now() - anchor.current.local;
      setClock(anchor.current.server + elapsed);
    };
    update();
    const timer = window.setInterval(update, 1_000);
    return () => window.clearInterval(timer);
  }, [evaluatedAt]);
  return clock;
}

function expiryLabel(expiresAt: string, now: number): {
  label: string;
  urgent: boolean;
  expired: boolean;
} {
  const expiry = Date.parse(expiresAt);
  if (!Number.isFinite(expiry)) {
    return { label: "Expiry not reported", urgent: false, expired: false };
  }
  const remaining = expiry - now;
  if (remaining <= 0) {
    return { label: `Expired ${timeAgo(expiresAt)}`, urgent: true, expired: true };
  }
  const minutes = Math.floor(remaining / 60_000);
  const seconds = Math.floor((remaining % 60_000) / 1_000);
  return {
    label: `Expires in ${minutes}:${String(seconds).padStart(2, "0")}`,
    urgent: remaining <= 5 * 60_000,
    expired: false,
  };
}

function DecisionQueueRow({
  item,
  index,
  selected,
  now,
  onSelect,
  register,
}: {
  item: DecisionQueueItem;
  index: number;
  selected: boolean;
  now: number;
  onSelect: () => void;
  register: (node: HTMLButtonElement | null) => void;
}) {
  const evidence = item.evidence_summary;
  const expiry = expiryLabel(item.expires_at, now);
  const owner = item.proposer_email ?? item.proposer_id ?? "Owner not reported";
  return (
    <li>
      <button
        ref={register}
        type="button"
        onClick={onSelect}
        aria-label={`Open ${actionTitle(item.action_type)} decision`}
        aria-current={selected ? "true" : undefined}
        className={`group w-full rounded-xl border p-3 text-left transition-colors sm:p-4 ${
          selected
            ? "border-accent bg-page shadow-sm"
            : "border-grid bg-page hover:border-accent/50 hover:bg-hairline/20"
        }`}
      >
        <div className="flex items-start gap-3">
          <span
            className={`grid h-7 w-7 shrink-0 place-items-center rounded-full text-xs font-semibold ${
              selected ? "bg-accent text-white" : "bg-hairline text-ink-2"
            }`}
            aria-hidden="true"
          >
            {index + 1}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="min-w-0 flex-1 text-sm font-semibold text-ink">
                {actionTitle(item.action_type)}
              </h3>
              <Badge tone={statusTone(item.risk)}>{item.risk} risk</Badge>
              {!item.can_approve && <Badge tone="warning">review only</Badge>}
            </div>
            <p className="mt-1.5 line-clamp-2 text-xs leading-5 text-ink-2">
              {impactSummary(item)}
            </p>
            <div className="mt-3 grid gap-2 text-[11px] text-muted sm:grid-cols-2">
              <span className="inline-flex items-center gap-1.5">
                <ListChecks className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                {item.target_count} exact target{item.target_count === 1 ? "" : "s"}
              </span>
              <span className="inline-flex items-center gap-1.5">
                <FileSearch className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                {evidence?.matched_count ?? 0} current evidence match
                {(evidence?.matched_count ?? 0) === 1 ? "" : "es"}
              </span>
              <span className="inline-flex min-w-0 items-center gap-1.5">
                <UserRound className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                <span className="truncate">{owner}</span>
              </span>
              <span
                className={`inline-flex items-center gap-1.5 font-medium ${
                  expiry.urgent ? "text-status-warning" : "text-muted"
                }`}
              >
                {expiry.expired ? (
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                ) : (
                  <Clock3 className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                )}
                {expiry.label}
              </span>
            </div>
          </div>
          <ArrowRight
            className="mt-1 h-4 w-4 shrink-0 text-muted transition-transform group-hover:translate-x-0.5"
            aria-hidden="true"
          />
        </div>
      </button>
    </li>
  );
}

function LegacyEvidenceList({
  rows,
  onAsk,
}: {
  rows: Row[];
  onAsk: (decision: LegacyDecision) => void;
}) {
  return (
    <ol className="space-y-3" aria-label="Ranked open evidence">
      {rows.map((row, index) => {
        const decision = normalizeLegacyDecision(row);
        return (
          <li key={rowText(row, "finding_id", "check_id") ?? `${decision.title}-${index}`}>
            <div className="rounded-xl border border-grid bg-page p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="grid h-7 w-7 place-items-center rounded-full bg-hairline text-xs font-semibold text-ink-2">
                  {index + 1}
                </span>
                <Badge tone={statusTone(decision.severity)}>{decision.pillar}</Badge>
                <h3 className="min-w-0 flex-1 text-sm font-semibold text-ink">
                  {decision.title}
                </h3>
              </div>
              <p className="mt-2 text-xs leading-5 text-ink-2">{decision.description}</p>
              <dl className="mt-3 grid gap-2 border-t border-grid pt-3 sm:grid-cols-3">
                <div>
                  <dt className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                    Evidence
                  </dt>
                  <dd className="mt-1 text-xs text-ink">{decision.evidence}</dd>
                </div>
                <div>
                  <dt className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                    Impact
                  </dt>
                  <dd className="mt-1 text-xs text-ink">{decision.impact}</dd>
                </div>
                <div>
                  <dt className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                    Control
                  </dt>
                  <dd className="mt-1 text-xs text-ink">
                    {decision.action ? "Exact plan required" : "Human review required"}
                  </dd>
                </div>
              </dl>
              <div className="mt-3 flex flex-wrap gap-2">
                {decision.action ? (
                  <PlanActionButton
                    action={decision.action}
                    label="Review exact plan"
                    tone="primary"
                  />
                ) : (
                  <Link
                    to="/actions"
                    className="inline-flex min-h-11 items-center rounded-lg bg-brand-mid px-3 py-2 text-xs font-semibold text-white hover:bg-brand-maroon"
                  >
                    Review in Action Center
                  </Link>
                )}
                <button
                  type="button"
                  onClick={() => onAsk(decision)}
                  className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-grid px-3 py-2 text-xs font-semibold text-ink hover:bg-hairline"
                >
                  <Bot className="h-3.5 w-3.5" aria-hidden="true" />
                  Ask why this matters
                </button>
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function CautiousZeroState({
  sources,
  expiredCount,
  runAt,
}: {
  sources: SourceHealth[];
  expiredCount: number;
  runAt?: string | null;
}) {
  const reachable = sources.filter(
    (source) => !["unavailable", "unknown"].includes(String(source.status).toLowerCase()),
  ).length;
  const coverageIncomplete =
    sources.length === 0 ||
    sources.some((source) =>
      ["degraded", "unavailable", "unknown", "stale"].includes(
        String(source.status).toLowerCase(),
      ),
    );
  return (
    <div className="rounded-xl border border-dashed border-grid bg-page p-4">
      <div className="flex items-start gap-3">
        {coverageIncomplete ? (
          <AlertTriangle
            className="mt-0.5 h-5 w-5 shrink-0 text-status-warning"
            aria-hidden="true"
          />
        ) : (
          <CheckCircle2
            className="mt-0.5 h-5 w-5 shrink-0 text-status-good"
            aria-hidden="true"
          />
        )}
        <div>
          <h3 className="text-sm font-semibold text-ink">No approval request is waiting.</h3>
          <p className="mt-1 text-xs leading-5 text-ink-2">
            This means no open decision was recorded—not that every possible check passed.
          </p>
        </div>
      </div>
      <dl className="mt-4 grid gap-3 sm:grid-cols-3">
        <div className="border-t border-grid pt-2">
          <dt className="text-[10px] font-semibold uppercase tracking-wide text-muted">
            Reachable sources
          </dt>
          <dd className="mt-1 text-sm font-semibold text-ink">
            {reachable} / {sources.length}
          </dd>
        </div>
        <div className="border-t border-grid pt-2">
          <dt className="text-[10px] font-semibold uppercase tracking-wide text-muted">
            Expired plans
          </dt>
          <dd className="mt-1 text-sm font-semibold text-ink">{expiredCount}</dd>
        </div>
        <div className="border-t border-grid pt-2">
          <dt className="text-[10px] font-semibold uppercase tracking-wide text-muted">
            Latest evidence
          </dt>
          <dd className="mt-1 text-sm font-semibold text-ink">
            {runAt ? timeAgo(runAt) : "Not reported"}
          </dd>
        </div>
      </dl>
      <p className="mt-4 flex items-center gap-1.5 text-[11px] text-muted">
        <Sparkles className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        {coverageIncomplete
          ? "Remaining work: restore incomplete source coverage, then wait for the next normalized collection."
          : "Remaining work: wait for the next normalized collection and review any newly recorded evidence."}
      </p>
    </div>
  );
}

export interface MissionControlProps {
  onAskDecision?: (item: DecisionQueueItem) => void;
}

export function MissionControl({ onAskDecision }: MissionControlProps = {}) {
  const openAssistant = useAssistantPanel();
  const { pending: assistantPending, send, setFocus } = useChat();
  const manualRefresh = useRef(false);
  const rowRefs = useRef(new Map<string, HTMLButtonElement>());
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [reviewId, setReviewId] = useState<string | null>(null);
  const closeSheet = useCallback(() => setSheetOpen(false), []);
  const closeReview = useCallback(() => setReviewId(null), []);
  const wide = useWideLayout();
  const query = useQuery({
    queryKey: ["mission-control"],
    queryFn: () => {
      const refresh = manualRefresh.current;
      manualRefresh.current = false;
      return fetchMissionControl(refresh);
    },
    staleTime: 15_000,
    refetchInterval: 30_000,
    retry: false,
  });

  const data = query.data?.envelope.data;
  const queueItems = useMemo(
    () => data?.decision_queue?.items ?? [],
    [data?.decision_queue?.items],
  );
  const serverNow = useServerClock(
    data?.decision_queue?.evaluated_at ?? query.data?.envelope.as_of,
  );
  const activeQueueItems = useMemo(
    () =>
      queueItems.filter(
        (item) =>
          item.effective_status.toUpperCase() !== "EXPIRED" &&
          (!Number.isFinite(Date.parse(item.expires_at)) ||
            Date.parse(item.expires_at) > serverNow),
      ),
    [queueItems, serverNow],
  );
  const selectedItem =
    selectedId === null
      ? activeQueueItems[0] ?? null
      : activeQueueItems.find((item) => item.action_id === selectedId) ?? null;

  useEffect(() => {
    if (activeQueueItems.length === 0) {
      setSelectedId(null);
      setSheetOpen(false);
      return;
    }
    if (
      !selectedId ||
      !activeQueueItems.some((item) => item.action_id === selectedId)
    ) {
      if (selectedId) setSheetOpen(false);
      setSelectedId(activeQueueItems[0]?.action_id ?? null);
    }
  }, [activeQueueItems, selectedId]);

  if (query.isPending) {
    return (
      <div className="space-y-5">
        <PageHeader
          eyebrow="AI Mission Control"
          title="Loading decision records…"
          description="Reading immutable approvals, current evidence, and source coverage."
        />
        <Card>
          <Skeleton rows={9} />
        </Card>
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="space-y-5">
        <PageHeader
          eyebrow="AI Mission Control"
          title="Decision records are unavailable."
          description="Mission Control fails closed when evidence or control-plane data cannot be read."
        />
        <ErrorState error={query.error} />
      </div>
    );
  }

  const { envelope, compatibility } = query.data;
  const mission = envelope.data;
  const findings = mission.findings?.data;
  const findingsTotal = Number(findings?.total ?? 0);
  const spendRows = mission.spend?.data ?? [];
  const spendTotal = spendRows.reduce((sum, row) => sum + Number(row.list_cost_usd ?? 0), 0);
  const decisions = mission.decisions ?? fallbackDecisions(mission);
  const outcomes = mission.outcomes ?? {};
  const queue = mission.decision_queue;
  const activeCount = queue ? activeQueueItems.length : 0;
  const locallyExpiredCount = queueItems.length - activeQueueItems.length;
  const expiredCount = (queue?.expired_count ?? 0) + locallyExpiredCount;
  const expiringSoonCount = activeQueueItems.filter((item) => {
    const expiry = Date.parse(item.expires_at);
    return Number.isFinite(expiry) && expiry - serverNow <= 5 * 60_000;
  }).length;
  const sources = Array.isArray(mission.data_health) ? mission.data_health : [];
  const nonHealthySources = sources.filter(
    (source) => String(source.status).toLowerCase() !== "healthy",
  );
  const degradedSources = sources.filter(
    (source) =>
      ["degraded", "unavailable", "unknown", "stale"].includes(
        String(source.status).toLowerCase(),
      ),
  );
  const normallyReportingSources = sources.length - nonHealthySources.length;
  const hasDegradedCoverage = degradedSources.length > 0 || sources.length === 0;
  const proposalOnly = sources.some(
    (source) => String(source.status).toLowerCase() === "proposal_only",
  );
  const heading =
    activeCount > 0
      ? "Decisions requiring you."
      : findingsTotal > 0
        ? "Open evidence needs review."
        : "No open findings recorded.";
  const description = hasDegradedCoverage
    ? "Coverage is incomplete. Review recorded decisions while unavailable sources recover."
    : activeCount > 0
      ? "Review exact plans, current evidence, and immutable history before approving a change."
      : findingsTotal > 0
        ? "Evidence is open, but no immutable action plan is waiting for approval."
        : "No open finding is recorded in the latest reporting cycle; this is not an all-clear.";

  const selectItem = (item: DecisionQueueItem) => {
    setSelectedId(item.action_id);
    if (!wide) setSheetOpen(true);
  };
  const reviewItem = (item: DecisionQueueItem) => {
    setSheetOpen(false);
    window.requestAnimationFrame(() => setReviewId(item.action_id));
  };
  const askItem = (item: DecisionQueueItem) => {
    setSheetOpen(false);
    window.requestAnimationFrame(() => {
      if (onAskDecision) {
        onAskDecision(item);
        return;
      }
      const assistantFocus = {
        actionId: item.action_id,
        label: actionTitle(item.action_type),
      };
      openAssistant(assistantFocus);
      send(
        `Explain action request ${item.action_id} (${actionTitle(item.action_type)}). ` +
          "Cite current evidence, impact, source freshness, expiry, and approval controls. " +
          "Remain read-only and do not execute anything.",
        assistantFocus,
      );
    });
  };
  const askLegacy = (decision: LegacyDecision) => {
    if (assistantPending) return;
    setFocus(null);
    openAssistant();
    send(
      `Why is "${decision.title}" important for this workspace? Explain the evidence, ` +
        "impact, source freshness, and approval control. Remain read-only.",
      null,
    );
  };

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="AI Mission Control"
        title={heading}
        description={description}
        actions={
          <AsOf
            asOf={envelope.as_of}
            cached={envelope.cached}
            onRefresh={() => {
              manualRefresh.current = true;
              return query.refetch();
            }}
            refreshing={query.isFetching}
          />
        }
      />

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge tone="info">
          {mission.scope?.workspace_name ?? mission.scope?.workspace ?? "Current workspace"}
        </Badge>
        <Badge tone="info">{mission.scope?.environment ?? "development"}</Badge>
        {mission.scope?.region && <Badge tone="info">{mission.scope.region}</Badge>}
        {compatibility && <Badge tone="warning">compatibility mode</Badge>}
        {proposalOnly && <Badge tone="warning">proposal only</Badge>}
        {expiringSoonCount > 0 ? (
          <Badge tone="warning">
            {expiringSoonCount} expiring within 5 minutes
          </Badge>
        ) : null}
      </div>

      <section aria-labelledby="decision-queue-title">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 id="decision-queue-title" className="text-base font-semibold text-ink">
              {activeQueueItems.length > 0 ? "Approval queue" : "Decision work"}
            </h2>
            <p className="mt-0.5 text-xs text-muted">
              {activeQueueItems.length > 0
                ? "Ranked by risk, expiry, creation time, then immutable action ID."
                : "Only recorded evidence and durable approval state are shown."}
            </p>
          </div>
          <Link
            to="/actions"
            className="inline-flex min-h-11 items-center gap-1 px-1 text-xs font-semibold text-accent hover:underline"
          >
            Open Action Center
            <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
          </Link>
        </div>

        {activeQueueItems.length > 0 ? (
          <div className="grid gap-4 xl:grid-cols-[minmax(22rem,0.82fr)_minmax(30rem,1.18fr)]">
            <Card className="min-w-0">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs text-muted">
                  <strong className="font-semibold text-ink">{activeCount}</strong> active
                  {expiredCount > 0 ? ` · ${expiredCount} expired` : ""}
                </p>
                <Badge tone="info">{queue?.ranking ?? "server-ranked"}</Badge>
              </div>
              <ol className="space-y-2" aria-label="Approval queue">
                {activeQueueItems.map((item, index) => (
                  <DecisionQueueRow
                    key={item.action_id}
                    item={item}
                    index={index}
                    selected={selectedItem?.action_id === item.action_id}
                    now={serverNow}
                    onSelect={() => selectItem(item)}
                    register={(node) => {
                      if (node) rowRefs.current.set(item.action_id, node);
                      else rowRefs.current.delete(item.action_id);
                    }}
                  />
                ))}
              </ol>
            </Card>

            {wide && selectedItem && (
              <MissionDecisionDetail
                item={selectedItem}
                title={actionTitle(selectedItem.action_type)}
                presentation="inline"
                onReview={() => reviewItem(selectedItem)}
                onAsk={() => askItem(selectedItem)}
              />
            )}
          </div>
        ) : (
          <Card>
            {decisions.length > 0 ? (
              <LegacyEvidenceList rows={decisions} onAsk={askLegacy} />
            ) : (
              <CautiousZeroState
                sources={sources}
                expiredCount={expiredCount}
                runAt={findings?.run_ts}
              />
            )}
          </Card>
        )}
      </section>

      {hasDegradedCoverage && (
        <div
          className="flex items-start gap-2 rounded-xl border border-status-warning/40 bg-status-warning/10 p-3 text-xs leading-5 text-ink-2"
          role="status"
        >
          <AlertTriangle
            className="mt-0.5 h-4 w-4 shrink-0 text-status-warning"
            aria-hidden="true"
          />
          <span>
            Source coverage is partial
            {degradedSources.length > 0
              ? `: ${degradedSources
                  .map(
                    (source) =>
                      `${source.source} (${String(source.status).replaceAll("_", " ")})`,
                  )
                  .join(", ")}.`
              : ". Source-level health has not been reported."}
          </span>
        </div>
      )}

      <section aria-labelledby="operational-posture-title">
        <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 id="operational-posture-title" className="text-sm font-semibold text-ink">
              Operational posture
            </h2>
            <p className="mt-0.5 text-xs text-muted">
              Compact domain counts from the latest stored reporting cycle.
            </p>
          </div>
          <Badge tone="info">4 evidence domains</Badge>
        </div>
        <Card className="p-0">
          <div className="grid divide-y divide-grid sm:grid-cols-2 sm:divide-x sm:divide-y-0 xl:grid-cols-4">
            {PILLARS.map(({ key, label, href, icon: Icon, description }) => {
              const outcome = outcomes[key] ?? fallbackOutcome(key, mission, spendTotal);
              const openFindings =
                outcome.open_findings ??
                (typeof outcome.value === "number" ? outcome.value : 0);
              return (
                <Link
                  key={key}
                  to={href}
                  aria-label={`Open ${label}`}
                  className="group flex min-h-24 items-center gap-3 p-4 hover:bg-hairline/30"
                >
                  <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-grid bg-page text-accent">
                    <Icon className="h-4 w-4" aria-hidden="true" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center justify-between gap-2">
                      <h3 className="flex items-center gap-1.5 text-sm font-semibold text-ink">
                        <HealthDot state={openFindings === 0 ? "good" : "warning"} />
                        {label}
                      </h3>
                      <ArrowRight
                        className="h-3.5 w-3.5 text-muted transition-transform group-hover:translate-x-0.5"
                        aria-hidden="true"
                      />
                    </span>
                    <span className="mt-1 block text-xs font-medium text-ink-2">
                      {openFindings === 0
                        ? "0 recorded"
                        : `${openFindings} finding${openFindings === 1 ? "" : "s"}`}
                    </span>
                    <span className="mt-0.5 block text-[10px] text-muted">
                      {outcome.summary ?? description}
                    </span>
                  </span>
                </Link>
              );
            })}
          </div>
        </Card>
      </section>

      <div className="grid gap-4 lg:grid-cols-[0.8fr_1.2fr]">
        <Card className="min-w-0">
          <SectionTitle
            title="Source reachability"
            subtitle="Freshness and partial coverage qualify every conclusion"
            right={
              <Badge
                tone={
                  sources.length > 0 && normallyReportingSources === sources.length
                    ? "good"
                    : "warning"
                }
              >
                {normallyReportingSources} / {sources.length}
              </Badge>
            }
          />
          <DataHealthList sources={sources} />
        </Card>
        <Card className="min-w-0">
          <SectionTitle
            title="Recent governed outcomes"
            subtitle="Succeeded requests from the append-only action ledger"
          />
          {(mission.changes ?? []).length > 0 ? (
            <DataTable
              rows={governedOutcomeRows(mission.changes ?? [])}
              pageSize={5}
              searchable={false}
              exportName="governed-outcomes"
              caption="Recent governed outcomes"
            />
          ) : (
            <EmptyState
              message="No succeeded governed action is recorded in this view."
              positive={false}
            />
          )}
        </Card>
      </div>

      <p className="flex items-center gap-1.5 text-[11px] text-muted">
        <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
        AI explanations remain read-only; exact plans, approvals, execution, and verification are
        deterministic and append-only.
      </p>

      {!wide && sheetOpen && selectedItem && (
        <MissionDecisionDetail
          item={selectedItem}
          title={actionTitle(selectedItem.action_type)}
          presentation="sheet"
          onClose={closeSheet}
          onReview={() => reviewItem(selectedItem)}
          onAsk={() => askItem(selectedItem)}
        />
      )}

      {reviewId && (
        <ActionReviewDialog
          actionId={reviewId}
          onClose={closeReview}
          onChanged={query.refetch}
        />
      )}
    </div>
  );
}

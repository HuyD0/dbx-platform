import { useQuery } from "@tanstack/react-query";
import {
  Bot,
  CheckCircle2,
  ChevronDown,
  FileCheck2,
  FileSearch,
  Fingerprint,
  ListChecks,
  ShieldCheck,
  UserRound,
  X,
} from "lucide-react";
import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { apiGet } from "../lib/api";
import { dateTime, timeAgo } from "../lib/format";
import type {
  ActionEvent,
  ActionRequestDetail,
  ActionTimelineItem,
  ActionTimelineStage,
  DecisionQueueItem,
} from "../lib/types";
import { DataTable } from "./DataTable";
import { Badge, EmptyState, ErrorState, Skeleton, statusTone } from "./ui";

type DetailTab = "overview" | "evidence" | "history";

const TABS: Array<{ id: DetailTab; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "evidence", label: "Evidence" },
  { id: "history", label: "History" },
];

const TERMINAL_STATUS = new Set([
  "SUCCEEDED",
  "FAILED",
  "ROLLED_BACK",
  "REJECTED",
  "EXPIRED",
  "STALE",
]);

function humanize(value: string): string {
  const words = value.replace(/[._-]+/g, " ").replace(/\s+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1).toLowerCase() : "Recorded event";
}

function stageForEvent(event: ActionEvent): ActionTimelineStage | null {
  const kind = event.event_type.toUpperCase();
  const status = event.to_status ?? "";
  if (kind.includes("PLAN")) return "plan";
  if (kind.includes("APPROV") || kind.includes("REJECT")) return "approval";
  if (kind.includes("EXECUT") || kind.includes("SUBMIT")) return "execution";
  if (kind.includes("VERIF")) return "verification";
  if (
    kind.includes("SUCCEED") ||
    kind.includes("FAIL") ||
    kind.includes("ROLLBACK") ||
    kind.includes("EXPIRE") ||
    TERMINAL_STATUS.has(status)
  ) {
    return "outcome";
  }
  return null;
}

/** Build a client-only view of events already present in the append-only record.
 * No missing lifecycle stage is inferred. */
export function buildActionTimeline(detail: ActionRequestDetail): ActionTimelineItem[] {
  const items: ActionTimelineItem[] = [];
  const events = detail.events;

  for (const approval of detail.approvals) {
    items.push({
      id: approval.approval_id,
      stage: "approval",
      label: humanize(approval.decision),
      timestamp: approval.decided_at,
      actor: approval.approver_email ?? approval.approver_id,
      status: approval.decision,
    });
  }

  for (const event of events) {
    const stage = stageForEvent(event);
    if (!stage) continue;
    items.push({
      id: event.event_id,
      stage,
      label: humanize(event.event_type),
      timestamp: event.event_ts,
      actor: event.actor_id,
      status: event.to_status,
      detail: Object.keys(event.details).length > 0 ? JSON.stringify(event.details) : null,
    });
  }

  return items.sort((left, right) => {
    const leftTime = left.timestamp ? Date.parse(left.timestamp) : Number.POSITIVE_INFINITY;
    const rightTime = right.timestamp ? Date.parse(right.timestamp) : Number.POSITIVE_INFINITY;
    return leftTime - rightTime;
  });
}

function TimelineIcon({ stage }: { stage: ActionTimelineStage }) {
  const icons: Record<ActionTimelineStage, typeof Fingerprint> = {
    plan: Fingerprint,
    approval: ShieldCheck,
    execution: ListChecks,
    verification: FileCheck2,
    outcome: CheckCircle2,
  };
  const Icon = icons[stage];
  return <Icon className="h-4 w-4" aria-hidden="true" />;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0 border-t border-grid pt-2">
      <dt className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted">{label}</dt>
      <dd className="mt-1 break-words text-xs leading-5 text-ink">{children}</dd>
    </div>
  );
}

function summarizeImpact(impact: unknown): Array<{ label: string; value: string }> {
  if (impact == null || typeof impact !== "object" || Array.isArray(impact)) return [];
  return Object.entries(impact)
    .map(([key, value]) => {
      if (typeof value === "number") return { label: humanize(key), value: value.toLocaleString() };
      if (typeof value === "string") return { label: humanize(key), value };
      if (typeof value === "boolean") return { label: humanize(key), value: value ? "Yes" : "No" };
      if (Array.isArray(value)) {
        return {
          label: humanize(key),
          value: `${value.length.toLocaleString()} item${value.length === 1 ? "" : "s"}`,
        };
      }
      if (value && typeof value === "object") {
        const fieldCount = Object.keys(value).length;
        return {
          label: humanize(key),
          value: `${fieldCount.toLocaleString()} field${fieldCount === 1 ? "" : "s"}`,
        };
      }
      return { label: humanize(key), value: "Not reported" };
    })
    .slice(0, 6);
}

function ImpactSummary({ impact }: { impact: unknown }) {
  const [expanded, setExpanded] = useState(false);
  const facts = summarizeImpact(impact);
  const raw = typeof impact === "string" ? impact : JSON.stringify(impact, null, 2);

  return (
    <div className="mb-px rounded-xl border border-grid bg-page p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted">
            Recorded impact
          </p>
          <p className="mt-1 text-xs text-muted">
            Summary first; expand the raw JSON only when you need audit-level detail.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          className="inline-flex items-center gap-1 rounded-lg border border-grid px-2 py-1 text-xs font-medium text-ink-2 hover:bg-hairline"
        >
          {expanded ? "Hide JSON" : "Show JSON"}
          <ChevronDown
            className={`h-3.5 w-3.5 transition ${expanded ? "rotate-180" : ""}`}
            aria-hidden="true"
          />
        </button>
      </div>
      {facts.length > 0 ? (
        <dl className="mt-3 grid gap-2 sm:grid-cols-2">
          {facts.map((fact) => (
            <div key={fact.label} className="rounded-lg bg-surface px-3 py-2">
              <dt className="text-[10px] font-semibold uppercase tracking-[0.1em] text-muted">
                {fact.label}
              </dt>
              <dd className="mt-1 text-sm font-medium text-ink">{fact.value}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="mt-3 text-xs text-ink-2">{raw}</p>
      )}
      {expanded && (
        <pre className="mt-3 max-h-80 overflow-auto rounded-lg border border-grid bg-surface p-3 font-mono text-[11px] leading-5 text-ink-2">
          {raw}
        </pre>
      )}
    </div>
  );
}

function DetailTabs({
  active,
  onChange,
  counts,
  id,
}: {
  active: DetailTab;
  onChange: (tab: DetailTab) => void;
  counts: Partial<Record<DetailTab, number>>;
  id: string;
}) {
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const current = TABS.findIndex((tab) => tab.id === active);
    let next = current;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = TABS.length - 1;
    if (event.key === "ArrowLeft") next = (current - 1 + TABS.length) % TABS.length;
    if (event.key === "ArrowRight") next = (current + 1) % TABS.length;
    const tab = TABS[next];
    if (!tab) return;
    onChange(tab.id);
    window.requestAnimationFrame(() => document.getElementById(`${id}-${tab.id}`)?.focus());
  };

  return (
    <div
      role="tablist"
      aria-label="Decision detail"
      onKeyDown={onKeyDown}
      className="flex gap-1 overflow-x-auto border-b border-grid"
    >
      {TABS.map((tab) => (
        <button
          key={tab.id}
          id={`${id}-${tab.id}`}
          type="button"
          role="tab"
          aria-selected={active === tab.id}
          aria-controls={`${id}-${tab.id}-panel`}
          tabIndex={active === tab.id ? 0 : -1}
          onClick={() => onChange(tab.id)}
          className={`min-h-11 shrink-0 border-b-2 px-3 text-xs font-semibold ${
            active === tab.id
              ? "border-accent text-ink"
              : "border-transparent text-muted hover:text-ink"
          }`}
        >
          {tab.label}
          {counts[tab.id] != null && (
            <span className="ml-1.5 rounded-full bg-hairline px-1.5 py-0.5 text-[10px] tabular-nums">
              {counts[tab.id]}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}

function Overview({
  detail,
  item,
}: {
  detail: ActionRequestDetail | undefined;
  item: DecisionQueueItem;
}) {
  const targets = detail?.targets ?? detail?.items ?? [];
  const impact = detail?.impact ?? item.impact;
  return (
    <div className="space-y-4">
      <dl className="grid gap-3 sm:grid-cols-2">
        <Field label="Exact targets">
          {item.target_count} immutable target{item.target_count === 1 ? "" : "s"}
        </Field>
        <Field label="Proposed by">
          {item.proposer_email ?? item.proposer_id ?? "Identity not reported"}
        </Field>
        <Field label="Created">
          <time dateTime={item.created_at} title={item.created_at}>
            {timeAgo(item.created_at)}
          </time>
        </Field>
        <Field label="Expires">
          <time dateTime={item.expires_at} title={item.expires_at}>
            {dateTime(item.expires_at)}
          </time>
        </Field>
      </dl>

      {impact != null && (
        <ImpactSummary impact={impact} />
      )}

      {targets.length > 0 ? (
        <div>
          <h3 className="mb-2 text-xs font-semibold text-ink">Immutable target snapshot</h3>
          <DataTable
            rows={targets}
            pageSize={5}
            searchable={false}
            exportable={false}
            caption={`Immutable targets for ${item.action_type}`}
          />
        </div>
      ) : (
        <EmptyState
          message="Target details are available in the exact-plan review."
          positive={false}
        />
      )}
    </div>
  );
}

function Evidence({ detail, item }: { detail: ActionRequestDetail | undefined; item: DecisionQueueItem }) {
  const evidence = detail?.evidence_correlation;
  const summary = item.evidence_summary;
  if (!evidence) {
    return (
      <EmptyState
        message={
          summary?.matched_count
            ? `${summary.matched_count} current evidence record${
                summary.matched_count === 1 ? "" : "s"
              } matched. Open exact-plan review while details finish loading.`
            : "No current evidence was correlated to these immutable targets."
        }
        positive={false}
      />
    );
  }
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <Badge tone={statusTone(evidence.coverage_status)}>
          {evidence.coverage_status.replaceAll("_", " ")} coverage
        </Badge>
        <span>
          {evidence.total} matched record{evidence.total === 1 ? "" : "s"}
        </span>
        {evidence.truncated && <span>Showing the first {evidence.items.length}</span>}
      </div>
      {evidence.items.length > 0 ? (
        <ol className="space-y-2">
          {evidence.items.map((finding, index) => {
            const observedAt = finding.freshness_at;
            const title =
              finding.check_name ?? finding.finding_id ?? `Evidence ${index + 1}`;
            return (
              <li
                key={`${finding.finding_id ?? "evidence"}-${index}`}
                className="rounded-xl border border-grid bg-page p-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <FileSearch className="h-4 w-4 shrink-0 text-accent" aria-hidden="true" />
                  <h3 className="min-w-0 flex-1 text-xs font-semibold text-ink">{title}</h3>
                  <Badge tone={statusTone(finding.severity)}>
                    {finding.severity ?? "severity unreported"}
                  </Badge>
                </div>
                <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-muted">
                  <span>{finding.match_type.replaceAll("_", " ")}</span>
                  {finding.pillar && <span>{finding.pillar}</span>}
                  {finding.state && <span>{finding.state}</span>}
                  {observedAt && (
                    <time dateTime={observedAt} title={observedAt}>
                      observed {timeAgo(observedAt)}
                    </time>
                  )}
                </div>
                {finding.reason && (
                  <p className="mt-2 text-xs leading-5 text-ink-2">{finding.reason}</p>
                )}
              </li>
            );
          })}
        </ol>
      ) : (
        <EmptyState message="No current evidence was correlated to these targets." positive={false} />
      )}
    </div>
  );
}

function HistoryView({ detail }: { detail: ActionRequestDetail | undefined }) {
  const timeline = useMemo(() => (detail ? buildActionTimeline(detail) : []), [detail]);
  if (timeline.length === 0) {
    return (
      <EmptyState
        message="No recorded approval, execution, verification, or outcome events are available."
        positive={false}
      />
    );
  }
  return (
    <ol className="space-y-0" aria-label="Recorded action history">
      {timeline.map((entry, index) => (
        <li key={entry.id} className="relative flex gap-3 pb-4 last:pb-0">
          {index < timeline.length - 1 && (
            <span
              className="absolute bottom-0 left-[15px] top-8 w-px bg-grid"
              aria-hidden="true"
            />
          )}
          <span className="relative grid h-8 w-8 shrink-0 place-items-center rounded-full border border-grid bg-page text-accent">
            <TimelineIcon stage={entry.stage} />
          </span>
          <div className="min-w-0 flex-1 pt-0.5">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-xs font-semibold text-ink">{entry.label}</h3>
              {entry.status && <Badge tone={statusTone(entry.status)}>{entry.status}</Badge>}
            </div>
            <p className="mt-1 text-[11px] text-muted">
              {entry.timestamp ? (
                <time dateTime={entry.timestamp} title={entry.timestamp}>
                  {timeAgo(entry.timestamp)}
                </time>
              ) : (
                "Time not reported"
              )}
              {entry.actor ? ` · ${entry.actor}` : ""}
            </p>
            {entry.detail && (
              <p className="mt-1 break-words text-xs leading-5 text-ink-2">{entry.detail}</p>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

export function MissionDecisionDetail({
  item,
  title,
  presentation,
  onClose,
  onReview,
  onAsk,
}: {
  item: DecisionQueueItem;
  title?: string;
  presentation: "inline" | "sheet";
  onClose?: () => void;
  onReview: () => void;
  onAsk: () => void;
}) {
  const [activeTab, setActiveTab] = useState<DetailTab>("overview");
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const tabsId = useId();
  const previousFocus = useRef<HTMLElement | null>(null);
  const query = useQuery({
    queryKey: ["action-request", item.action_id],
    queryFn: () => apiGet<ActionRequestDetail>(`/api/action-requests/${item.action_id}`),
    retry: false,
  });
  const detail = query.data;
  const displayTitle = title ?? humanize(item.action_type);
  const evidenceCount =
    detail?.evidence_correlation?.total ??
    item.evidence_summary.matched_count ??
    0;
  const timelineCount = detail ? buildActionTimeline(detail).length : 0;

  useEffect(() => {
    setActiveTab("overview");
  }, [item.action_id]);

  useEffect(() => {
    if (presentation !== "sheet" || !onClose) return;
    previousFocus.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.requestAnimationFrame(() => closeRef.current?.focus());
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !panelRef.current) return;
      const focusable = Array.from(
        panelRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = originalOverflow;
      document.removeEventListener("keydown", onKeyDown);
      previousFocus.current?.focus();
    };
  }, [onClose, presentation]);

  const panel = (
    <div
      ref={panelRef}
      role={presentation === "sheet" ? "dialog" : "region"}
      aria-modal={presentation === "sheet" ? "true" : undefined}
      aria-labelledby={titleId}
      className={
        presentation === "sheet"
          ? "fixed inset-y-0 right-0 z-50 flex w-full max-w-2xl flex-col overflow-hidden border-l border-grid bg-surface shadow-2xl sm:w-[min(92vw,42rem)]"
          : "flex min-h-[36rem] flex-col overflow-hidden rounded-2xl border border-grid bg-surface"
      }
    >
      <div className="border-b border-grid bg-page px-4 py-4">
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-accent">
              Exact governed request
            </p>
            <h2 id={titleId} className="mt-1 text-base font-semibold text-ink">
              {displayTitle}
            </h2>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Badge tone={statusTone(item.effective_status)}>
                {item.effective_status.replaceAll("_", " ")}
              </Badge>
              <Badge tone={statusTone(item.risk)}>{item.risk} risk</Badge>
              <span className="font-mono text-[10px] text-muted">{item.action_id}</span>
            </div>
          </div>
          {presentation === "sheet" && onClose && (
            <button
              ref={closeRef}
              type="button"
              onClick={onClose}
              aria-label="Close decision details"
              className="grid min-h-11 min-w-11 place-items-center rounded-lg text-muted hover:bg-hairline hover:text-ink"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          )}
        </div>
      </div>

      <DetailTabs
        active={activeTab}
        onChange={setActiveTab}
        counts={{ evidence: evidenceCount, history: timelineCount }}
        id={tabsId}
      />

      <div
        id={`${tabsId}-${activeTab}-panel`}
        role="tabpanel"
        aria-labelledby={`${tabsId}-${activeTab}`}
        tabIndex={0}
        className="min-h-0 flex-1 overflow-y-auto p-4"
      >
        {query.isError ? (
          <>
            <ErrorState error={query.error} />
            {activeTab === "overview" && (
              <div className="mt-4">
                <Overview detail={undefined} item={item} />
              </div>
            )}
          </>
        ) : query.isPending && activeTab !== "overview" ? (
          <Skeleton rows={6} />
        ) : (
          <>
            {activeTab === "overview" && <Overview detail={detail} item={item} />}
            {activeTab === "evidence" && <Evidence detail={detail} item={item} />}
            {activeTab === "history" && <HistoryView detail={detail} />}
          </>
        )}
      </div>

      {item.effective_status.toUpperCase() === "EXPIRED" && (
        <p className="mx-4 mb-3 rounded-lg border border-status-warning/40 bg-status-warning/10 p-3 text-xs leading-5 text-ink-2">
          This immutable plan has expired and cannot be approved. Create a new exact plan from
          current evidence if the action is still required.
        </p>
      )}
      {!item.can_approve && item.effective_status.toUpperCase() !== "EXPIRED" && (
        <p className="mx-4 mb-3 rounded-lg border border-warning-accent bg-warning-surface p-3 text-xs leading-5 text-brand-maroon dark:text-status-warning">
          {detail?.actions_enabled === false
            ? "This deployment is proposal-only. You can inspect the immutable request, but approval remains unavailable until the governed executor and approver controls are connected."
            : detail
              ? "Approval is not available for your current identity or for this request state. You can still inspect the immutable request and its evidence."
              : "Approval readiness is being checked. You can inspect the immutable request while the detail record loads."}
        </p>
      )}

      <div className="grid gap-2 border-t border-grid bg-page p-4 sm:grid-cols-2">
        <button
          type="button"
          onClick={onReview}
          className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-brand-mid px-4 py-2 text-sm font-semibold text-white hover:bg-brand-maroon"
        >
          <ShieldCheck className="h-4 w-4" aria-hidden="true" />
          Review exact plan
        </button>
        <button
          type="button"
          onClick={onAsk}
          className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-grid px-4 py-2 text-sm font-semibold text-ink hover:bg-hairline"
        >
          <Bot className="h-4 w-4" aria-hidden="true" />
          Ask agent
          <span className="sr-only">about {displayTitle}</span>
        </button>
        <p className="flex items-center gap-1.5 text-[10px] text-muted sm:col-span-2">
          <UserRound className="h-3.5 w-3.5" aria-hidden="true" />
          Read-only — cannot execute changes
        </p>
      </div>
    </div>
  );

  if (presentation === "inline") return panel;
  return createPortal(
    <div
      className="fixed inset-0 z-40 bg-black/45"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose?.();
      }}
    >
      {panel}
    </div>,
    document.body,
  );
}

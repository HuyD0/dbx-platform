import {
  AlertTriangle,
  CheckCircle2,
  CircleHelp,
  Clock3,
  DatabaseZap,
  Info,
  RefreshCw,
} from "lucide-react";
import { useId, useState, type KeyboardEvent, type ReactNode } from "react";
import { ApiError } from "../lib/types";
import type { SourceHealth } from "../lib/types";
import { timeAgo } from "../lib/format";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`glass rounded-2xl p-4 ${className}`}>
      {children}
    </div>
  );
}

export function SectionTitle({
  title,
  subtitle,
  right,
}: {
  title: string;
  subtitle?: string;
  right?: ReactNode;
}) {
  return (
    <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
      </div>
      {right}
    </div>
  );
}

export function HelpTip({ label, children }: { label: string; children: ReactNode }) {
  const tooltipId = useId();
  return (
    <span className="group relative inline-flex align-middle">
      <button
        type="button"
        aria-label={label}
        aria-describedby={tooltipId}
        className="rounded-full p-0.5 text-muted hover:bg-hairline hover:text-ink focus-visible:text-ink"
      >
        <CircleHelp className="h-3.5 w-3.5" />
      </button>
      <span
        id={tooltipId}
        role="tooltip"
        className="pointer-events-none invisible absolute left-1/2 top-full z-20 mt-1.5 w-64 -translate-x-1/2 rounded-lg bg-ink px-2.5 py-2 text-left text-[11px] font-normal leading-4 text-page opacity-0 shadow-xl transition-opacity group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100"
      >
        {children}
      </span>
    </span>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4 pb-1">
      <div className="max-w-3xl">
        {eyebrow && (
          <p className="mb-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-accent">
            {eyebrow}
          </p>
        )}
        <h1 className="text-2xl font-semibold tracking-tight text-ink sm:text-3xl">{title}</h1>
        <p className="mt-1.5 text-sm leading-6 text-ink-2">{description}</p>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function StatTile({
  label,
  value,
  tone = "default",
  hint,
  size = "default",
  indicator,
}: {
  label: string;
  value: ReactNode;
  tone?: "default" | "good" | "warning" | "serious" | "critical";
  hint?: string;
  /** "hero" renders an oversized primary number for top-level health tiles. */
  size?: "default" | "hero";
  /** Optional status affordance (e.g. HealthDot) shown beside the label. */
  indicator?: ReactNode;
}) {
  const tones: Record<string, string> = {
    default: "text-ink",
    good: "text-status-good",
    warning: "text-status-warning",
    serious: "text-status-serious",
    critical: "text-status-critical",
  };
  const valueSize =
    size === "hero"
      ? "text-4xl sm:text-5xl font-bold tracking-tight"
      : "text-2xl font-semibold";
  return (
    <Card className="h-full">
      <div className="flex items-center gap-1.5 text-xs font-medium text-muted">
        {indicator}
        {label}
      </div>
      <div className={`mt-1 tabular-nums ${valueSize} ${tones[tone]}`}>{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </Card>
  );
}

/** Bento layout: a modular, asymmetric grid on the page canvas. Individual
 * cells rest in white `Card` containers (1px sand border) via `BentoCell`.
 * The default 12-column track lets cells claim asymmetric widths on wide
 * external monitors while collapsing to a single column on small screens. */
export function Bento({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`grid grid-cols-1 gap-4 lg:grid-cols-12 ${className}`}>{children}</div>
  );
}

/** One bento tile. `span`/`rowSpan` are Tailwind column/row-span utility
 * strings so callers control the asymmetry; content either supplies its own
 * `Card` or opts into the built-in one via `bare={false}` (default). */
export function BentoCell({
  children,
  span = "lg:col-span-6",
  rowSpan = "",
  bare = false,
  className = "",
}: {
  children: ReactNode;
  span?: string;
  rowSpan?: string;
  /** When true, render only the grid cell — the child brings its own surface. */
  bare?: boolean;
  className?: string;
}) {
  const cell = `${span} ${rowSpan}`.trim();
  if (bare) {
    return <div className={`${cell} min-w-0 ${className}`}>{children}</div>;
  }
  return (
    <div className={`${cell} min-w-0`}>
      <Card className={`h-full ${className}`}>{children}</Card>
    </div>
  );
}

/** Compact operational-health indicator. `live`/`good` use the bright decorative
 * health accent as a FILL only; any adjacent text stays on status tokens. */
export function HealthDot({
  state = "good",
  label,
}: {
  state?: "live" | "good" | "warning" | "critical" | "idle";
  label?: string;
}) {
  const fills: Record<string, string> = {
    good: "bg-health-accent",
    warning: "bg-warning-accent",
    critical: "bg-brand-primary",
    idle: "bg-muted",
  };
  // When no label is given the dot is purely decorative — hide it from the
  // accessibility tree so it never leaks into a parent heading's name.
  const semantics = label
    ? ({ role: "img", "aria-label": label, title: label } as const)
    : ({ "aria-hidden": true } as const);
  if (state === "live") {
    return <span className="pulse-dot" {...semantics} />;
  }
  return (
    <span
      className={`inline-block h-2 w-2 shrink-0 rounded-full ${fills[state]}`}
      {...semantics}
    />
  );
}

const badgeTones: Record<string, string> = {
  critical: "border border-status-critical/30 bg-critical-surface text-status-critical",
  serious: "border border-status-serious/30 bg-serious-surface text-status-serious",
  warning:
    "border border-warning-accent bg-warning-surface text-brand-maroon dark:text-status-warning",
  good: "border border-status-good/30 bg-success-surface text-status-good",
  info: "border border-status-info/30 bg-info-surface text-status-info",
};

export function Badge({
  tone,
  children,
}: {
  tone: keyof typeof badgeTones;
  children: ReactNode;
}) {
  const icon =
    tone === "good" ? (
      <CheckCircle2 className="h-3 w-3" />
    ) : tone === "info" ? (
      <Info className="h-3 w-3" />
    ) : (
      <AlertTriangle className="h-3 w-3" />
    );
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${badgeTones[tone]}`}
    >
      {icon}
      {children}
    </span>
  );
}

export function statusTone(
  status: unknown,
): "critical" | "serious" | "warning" | "good" | "info" {
  const value = String(status ?? "").toLowerCase();
  if (["failed", "critical", "unavailable", "rejected", "high"].some((s) => value.includes(s))) {
    return "critical";
  }
  if (["stale", "expired", "rollback", "serious"].some((s) => value.includes(s))) {
    return "serious";
  }
  if (
    [
      "pending",
      "awaiting",
      "attention",
      "warning",
      "degraded",
      "medium",
      "partial",
      "expiring",
      "executing",
      "verifying",
    ].some((s) => value.includes(s))
  ) {
    return "warning";
  }
  if (
    value === "on" ||
    ["healthy", "success", "approved", "good"].some((s) => value.includes(s))
  ) {
    return "good";
  }
  return "info";
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="animate-pulse space-y-2" role="status" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-4 rounded bg-hairline" />
      ))}
    </div>
  );
}

export function EmptyState({
  message,
  positive = true,
}: {
  message: string;
  positive?: boolean;
}) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-dashed border-grid px-3 py-4 text-sm text-muted">
      {positive ? (
        <CheckCircle2 className="h-4 w-4 shrink-0 text-status-good" />
      ) : (
        <CircleHelp className="h-4 w-4 shrink-0 text-muted" />
      )}
      {message}
    </div>
  );
}

const errorGuidance: Record<string, string> = {
  system_tables_unavailable:
    "System tables are not enabled or not granted to the app's identity.",
  warehouse_not_configured: "No SQL warehouse is configured for this deployment.",
  findings_table_missing:
    "Mission Control tables are not migrated yet. Run the reviewed deployment migration job.",
  permission_missing: "The app's identity lacks a permission for this check.",
  unauthenticated: "Your Databricks user identity could not be verified.",
  unauthorized: "Your identity is not authorized for this governed operation.",
  control_plane_unavailable: "Mission Control storage is temporarily unavailable.",
  agent_unavailable: "The backend LangGraph agent is not reachable.",
  query_timeout: "The warehouse query timed out — try refresh, or check the warehouse.",
  pricing_snapshot_missing: "Cost Planner needs its first price snapshot.",
  pricing_snapshot_unavailable: "Cost Planner pricing is not ready yet.",
};

export function ErrorState({ error }: { error: unknown }) {
  const apiErr = error instanceof ApiError ? error : null;
  const unavailable = apiErr && [404, 405, 501].includes(apiErr.status);
  // A gateway/proxy response carries no typed app body, so api.ts labels it with
  // the generic "http_error" code. On a transient status (502/503/504) this
  // means the warehouse is likely cold-starting or the backend was briefly
  // unreachable — a retry, not a broken data source. Typed app 503s keep their
  // own code (e.g. system_tables_unavailable) and are unaffected.
  const transient =
    !!apiErr && apiErr.code === "http_error" && [502, 503, 504].includes(apiErr.status);
  const neutral = unavailable || transient;
  const title = unavailable
    ? "This capability is not connected in this deployment."
    : transient
      ? "The data source is temporarily unavailable."
      : apiErr
        ? (errorGuidance[apiErr.code] ?? "The data source could not be read.")
        : "The data source could not be read.";
  const detail = apiErr ? apiErr.message : String(error);
  return (
    <div
      className={`rounded-lg border px-3 py-3 text-sm ${
        neutral
          ? "border-grid bg-hairline/30"
          : "border-status-serious/30 bg-status-serious/5"
      }`}
      role="status"
    >
      <div
        className={`flex items-center gap-2 font-medium ${
          neutral ? "text-ink-2" : "text-status-serious"
        }`}
      >
        {unavailable ? (
          <DatabaseZap className="h-4 w-4 shrink-0" />
        ) : transient ? (
          <Clock3 className="h-4 w-4 shrink-0" />
        ) : (
          <AlertTriangle className="h-4 w-4 shrink-0" />
        )}
        {title}
      </div>
      <p className="mt-1 text-xs text-muted">
        {unavailable
          ? "The interface is ready and will populate when its backend endpoint is enabled."
          : transient
            ? "The warehouse may be waking up, or the backend was briefly unreachable. Wait a moment, then refresh."
            : apiErr?.code.startsWith("pricing_snapshot")
              ? "Ask an approver to run the estimator-prices-pull job once. Your answers are saved on this screen, so you can retry after pricing is ready."
              : "Check data access and source health, then try refreshing."}
      </p>
      {apiErr?.hint && <p className="mt-1 text-xs text-muted">{apiErr.hint}</p>}
      {!unavailable && detail && (
        <details className="mt-2 text-xs text-muted">
          <summary className="cursor-pointer select-none hover:text-ink-2">Technical detail</summary>
          <p className="mt-1 max-h-28 overflow-auto break-words rounded bg-hairline/40 p-2 font-mono">
            {detail.slice(0, 500)}
            {detail.length > 500 ? "…" : ""}
          </p>
        </details>
      )}
    </div>
  );
}

export function CapabilityNotice({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="rounded-xl border border-grid bg-hairline/20 p-4" role="status">
      <div className="flex items-center gap-2 text-sm font-medium text-ink">
        <DatabaseZap className="h-4 w-4 text-accent" />
        {title}
      </div>
      <p className="mt-1 text-xs leading-5 text-muted">{description}</p>
    </div>
  );
}

export function DataHealthList({ sources }: { sources: SourceHealth[] }) {
  if (sources.length === 0) {
    return (
      <CapabilityNotice
        title="Coverage has not been reported"
        description="Source-level freshness and retention will appear after the next successful collection."
      />
    );
  }
  return (
    <ul className="grid gap-2 sm:grid-cols-2" aria-label="Data source health">
      {sources.map((source) => (
        <li key={source.source} className="rounded-xl border border-grid bg-page/30 p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-xs font-medium text-ink">{source.source}</span>
            <Badge tone={statusTone(source.status)}>{source.status}</Badge>
          </div>
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted">
            {source.freshness && (
              <span className="inline-flex items-center gap-1">
                <Clock3 className="h-3 w-3" />
                {timeAgo(source.freshness)}
              </span>
            )}
            {source.retention_days != null && <span>{source.retention_days}d retention</span>}
          </div>
          {source.notes && <p className="mt-1 text-[11px] leading-4 text-muted">{source.notes}</p>}
        </li>
      ))}
    </ul>
  );
}

export interface TabOption {
  id: string;
  label: string;
  badge?: number;
}

export function Tabs({
  tabs,
  active,
  onChange,
  label,
}: {
  tabs: TabOption[];
  active: string;
  onChange: (id: string) => void;
  label: string;
}) {
  const id = useId();
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const current = tabs.findIndex((tab) => tab.id === active);
    let next = current;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = tabs.length - 1;
    if (event.key === "ArrowLeft") next = (current - 1 + tabs.length) % tabs.length;
    if (event.key === "ArrowRight") next = (current + 1) % tabs.length;
    const tab = tabs[next];
    if (tab) {
      onChange(tab.id);
      window.requestAnimationFrame(() =>
        document.getElementById(`${id}-${tab.id}`)?.focus(),
      );
    }
  };
  return (
    <div
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
      className="flex gap-1 overflow-x-auto rounded-xl border border-grid bg-hairline/20 p-1"
    >
      {tabs.map((tab) => (
        <button
          key={tab.id}
          id={`${id}-${tab.id}`}
          type="button"
          role="tab"
          aria-selected={active === tab.id}
          tabIndex={active === tab.id ? 0 : -1}
          onClick={() => onChange(tab.id)}
          className={`inline-flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium ${
            active === tab.id
              ? "bg-surface text-ink shadow-sm"
              : "text-muted hover:bg-hairline hover:text-ink-2"
          }`}
        >
          {tab.label}
          {tab.badge != null && (
            <span className="rounded-full bg-hairline px-1.5 text-[10px] tabular-nums">
              {tab.badge}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}

export function AsOf({
  asOf,
  cached,
  onRefresh,
  refreshing,
}: {
  asOf?: string;
  cached?: boolean;
  onRefresh: () => Promise<unknown>;
  refreshing: boolean;
}) {
  const [requestPending, setRequestPending] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const refresh = () => {
    setRequestPending(true);
    setAnnouncement("Refreshing data.");
    void onRefresh().then(
      () => setAnnouncement("Refresh complete."),
      () => setAnnouncement("Refresh failed. Existing data remains on screen."),
    ).finally(() => setRequestPending(false));
  };
  return (
    <div className="flex items-center gap-2 text-xs text-muted">
      {asOf && (
        <span>
          as of {timeAgo(asOf)}
          {cached ? " (cached)" : ""}
        </span>
      )}
      <button
        type="button"
        onClick={refresh}
        title="Refresh"
        aria-label="Refresh"
        className="grid min-h-6 min-w-6 place-items-center rounded p-1 hover:bg-hairline disabled:opacity-50"
        disabled={refreshing || requestPending}
      >
        <RefreshCw
          className={`h-3.5 w-3.5 ${refreshing || requestPending ? "animate-spin" : ""}`}
        />
      </button>
      <span className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {announcement}
      </span>
    </div>
  );
}

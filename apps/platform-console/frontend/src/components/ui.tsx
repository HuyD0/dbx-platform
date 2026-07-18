import { AlertTriangle, CheckCircle2, Info, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import { ApiError } from "../lib/types";
import { timeAgo } from "../lib/format";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={`rounded-xl border border-hairline bg-surface p-4 shadow-sm ${className}`}
    >
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
    <div className="mb-3 flex items-start justify-between gap-3">
      <div>
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
      </div>
      {right}
    </div>
  );
}

export function StatTile({
  label,
  value,
  tone = "default",
  hint,
}: {
  label: string;
  value: ReactNode;
  tone?: "default" | "good" | "warning" | "serious" | "critical";
  hint?: string;
}) {
  const tones: Record<string, string> = {
    default: "text-ink",
    good: "text-status-good",
    warning: "text-status-warning",
    serious: "text-status-serious",
    critical: "text-status-critical",
  };
  return (
    <Card>
      <div className="text-xs font-medium text-muted">{label}</div>
      <div className={`mt-1 text-2xl font-semibold ${tones[tone]}`}>{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </Card>
  );
}

const badgeTones: Record<string, string> = {
  critical: "bg-status-critical/15 text-status-critical",
  serious: "bg-status-serious/15 text-status-serious",
  warning: "bg-status-warning/15 text-status-warning",
  good: "bg-status-good/15 text-status-good",
  info: "bg-hairline text-ink-2",
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

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="animate-pulse space-y-2" role="status" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-4 rounded bg-hairline" />
      ))}
    </div>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-dashed border-grid px-3 py-4 text-sm text-muted">
      <CheckCircle2 className="h-4 w-4 shrink-0 text-status-good" />
      {message}
    </div>
  );
}

const errorGuidance: Record<string, string> = {
  system_tables_unavailable:
    "System tables are not enabled or not granted to the app's identity.",
  warehouse_not_configured: "No SQL warehouse is configured for this deployment.",
  findings_table_missing:
    "The findings tables don't exist yet — run the dashboards-setup job first.",
  permission_missing: "The app's identity lacks a permission for this check.",
  agent_unavailable: "The platform agent's serving endpoint is not reachable.",
  query_timeout: "The warehouse query timed out — try refresh, or check the warehouse.",
};

export function ErrorState({ error }: { error: unknown }) {
  const apiErr = error instanceof ApiError ? error : null;
  const title = apiErr ? (errorGuidance[apiErr.code] ?? "Request failed.") : "Request failed.";
  const detail = apiErr ? apiErr.message : String(error);
  return (
    <div className="rounded-lg border border-status-serious/30 bg-status-serious/5 px-3 py-3 text-sm">
      <div className="flex items-center gap-2 font-medium text-status-serious">
        <AlertTriangle className="h-4 w-4 shrink-0" />
        {title}
      </div>
      <p className="mt-1 break-words text-xs text-ink-2">{detail}</p>
      {apiErr?.hint && <p className="mt-1 text-xs text-muted">{apiErr.hint}</p>}
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
  onRefresh: () => void;
  refreshing: boolean;
}) {
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
        onClick={onRefresh}
        title="Refresh"
        aria-label="Refresh"
        className="rounded p-1 hover:bg-hairline disabled:opacity-50"
        disabled={refreshing}
      >
        <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
      </button>
    </div>
  );
}

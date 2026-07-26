import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Tags,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { Link } from "react-router-dom";
import { Badge, Card } from "../ui";
import { currency, percent } from "../../lib/format";
import type {
  ApplicationSummary,
  ApplicationWindow,
} from "../../types/applications";

function coverageTone(value: number | null) {
  if (value == null) return "info" as const;
  if (value >= 90) return "good" as const;
  if (value >= 60) return "warning" as const;
  return "serious" as const;
}

export function CoverageMeter({
  value,
  label = "Exact attribution",
}: {
  value: number | null;
  label?: string;
}) {
  const bounded = Math.max(0, Math.min(100, value ?? 0));
  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-2 text-[11px]">
        <span className="text-muted">{label}</span>
        <span className="tabular-nums text-ink">
          {value == null ? "Not measured" : percent(value)}
        </span>
      </div>
      <div
        className="h-1.5 overflow-hidden rounded-full bg-hairline"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={value == null ? undefined : bounded}
        aria-valuetext={value == null ? "Not measured" : percent(value)}
      >
        <div
          className={`h-full rounded-full ${
            value == null
              ? "bg-muted"
              : value >= 90
                ? "bg-health-accent"
                : value >= 60
                  ? "bg-warning-accent"
                  : "bg-brand-primary"
          }`}
          style={{ width: `${value == null ? 0 : bounded}%` }}
        />
      </div>
    </div>
  );
}

export function ApplicationPortfolioCard({
  summary,
  window,
}: {
  summary: ApplicationSummary;
  window: ApplicationWindow;
}) {
  const detailHref = `/applications/${encodeURIComponent(
    summary.application_key,
  )}?window=${window}d`;
  return (
    <Card className="flex h-full min-w-0 flex-col p-0">
      <div className="flex items-start justify-between gap-3 border-b border-grid p-4">
        <div className="min-w-0">
          <h2>
            <Link
              to={detailHref}
              className="group inline-flex max-w-full items-center gap-1 text-base font-semibold text-ink hover:text-accent"
            >
              <span className="truncate">{summary.display_name}</span>
              <ArrowRight className="h-4 w-4 shrink-0 transition-transform group-hover:translate-x-0.5" />
            </Link>
          </h2>
          <p className="mt-0.5 truncate font-mono text-[11px] text-muted">
            {summary.application_key}
          </p>
        </div>
        <Badge tone={coverageTone(summary.coverage_pct)}>
          {summary.coverage_pct == null
            ? "Coverage pending"
            : `${Math.round(summary.coverage_pct)}% exact`}
        </Badge>
      </div>

      <div className="grid gap-2 p-4 sm:grid-cols-2">
        {summary.ledgers.length === 0 ? (
          <p className="col-span-full text-sm text-muted">
            No exact attributed cost in this window.
          </p>
        ) : (
          summary.ledgers.map((ledger) => (
            <div
              key={ledger.id}
              className="min-w-0 rounded-xl border border-grid bg-page/30 p-3"
            >
              <p className="truncate text-[11px] font-medium text-muted">
                {ledger.title}
              </p>
              <p className="mt-1 break-words text-lg font-semibold tabular-nums text-ink">
                {currency(ledger.amount, ledger.currency)}
              </p>
              <p className="mt-0.5 truncate text-[10px] uppercase tracking-wide text-muted">
                {ledger.pricing_basis.replaceAll("_", " ")}
              </p>
              {ledger.trusted === false && (
                <p className="mt-1 text-[10px] font-medium text-status-warning">
                  {ledger.status?.replaceAll("_", " ") ?? "Partial"} evidence ·
                  not a current exact claim
                </p>
              )}
              {ledger.trend_pct != null && (
                <p
                  className={`mt-2 inline-flex items-center gap-1 text-[10px] ${
                    ledger.trend_pct > 0
                      ? "text-status-serious"
                      : "text-status-good"
                  }`}
                >
                  {ledger.trend_pct > 0 ? (
                    <TrendingUp className="h-3 w-3" />
                  ) : (
                    <TrendingDown className="h-3 w-3" />
                  )}
                  {ledger.trend_pct > 0 ? "+" : ""}
                  {percent(ledger.trend_pct)} in this window
                </p>
              )}
            </div>
          ))
        )}
      </div>

      <div className="mt-auto space-y-3 px-4 pb-4">
        <CoverageMeter value={summary.coverage_pct} />
        {summary.trend_pct != null &&
          summary.ledgers.length === 1 &&
          summary.ledgers[0]?.trend_pct == null && (
          <p
            className={`inline-flex items-center gap-1 text-[11px] ${
              summary.trend_pct > 0
                ? "text-status-serious"
                : "text-status-good"
            }`}
          >
            {summary.trend_pct > 0 ? (
              <TrendingUp className="h-3 w-3" />
            ) : (
              <TrendingDown className="h-3 w-3" />
            )}
            {summary.trend_pct > 0 ? "+" : ""}
            {percent(summary.trend_pct)} exact cost trend
          </p>
        )}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
          <span className="inline-flex items-center gap-1">
            {summary.tag_health.conflicts > 0 ? (
              <AlertTriangle className="h-3 w-3 text-status-warning" />
            ) : (
              <CheckCircle2 className="h-3 w-3 text-status-good" />
            )}
            {summary.tag_health.conflicts} conflicts
          </span>
          <span className="inline-flex items-center gap-1">
            <Tags className="h-3 w-3" />
            {summary.tag_health.missing} missing tags
          </span>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {summary.environments.map((environment) => (
            <span
              key={environment}
              className="rounded-full border border-grid px-2 py-0.5 text-[10px] text-ink-2"
            >
              {environment}
            </span>
          ))}
          {summary.sources.map((source) => (
            <span
              key={source}
              className="rounded-full bg-hairline px-2 py-0.5 text-[10px] text-muted"
            >
              {source}
            </span>
          ))}
        </div>
      </div>
    </Card>
  );
}

import { AlertTriangle, CheckCircle2, CircleHelp } from "lucide-react";
import { currency } from "../../lib/format";
import type {
  ApplicationCoverage,
  ApplicationSourceHealth,
  ApplicationUnallocatedCost,
  TagAlignment,
  TagAlignmentStatus,
} from "../../types/applications";
import {
  Badge,
  Card,
  DataHealthList,
  EmptyState,
  SectionTitle,
} from "../ui";
import { CoverageMeter } from "./ApplicationPortfolioCard";

function humanize(value: string) {
  return value
    .toLowerCase()
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function alignmentTone(status: TagAlignmentStatus) {
  if (status === "matched") return "good" as const;
  if (status === "conflict") return "serious" as const;
  return "warning" as const;
}

function alignmentIcon(status: TagAlignmentStatus) {
  if (status === "matched") {
    return <CheckCircle2 className="h-3.5 w-3.5 text-status-good" />;
  }
  if (status === "conflict") {
    return <AlertTriangle className="h-3.5 w-3.5 text-status-serious" />;
  }
  return <CircleHelp className="h-3.5 w-3.5 text-status-warning" />;
}

export function ApplicationCoveragePanel({
  coverage,
  unallocated,
}: {
  coverage: ApplicationCoverage[];
  unallocated: ApplicationUnallocatedCost[];
}) {
  return (
    <Card>
      <SectionTitle
        title="Attribution coverage"
        subtitle="Exact evidence is separated from conflicts, shared spend, and unattributed rows"
      />
      {coverage.length === 0 ? (
        <EmptyState
          positive={false}
          message="Coverage has not been measured for this application."
        />
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          {coverage.map((item) => (
            <article
              key={`${item.source}-${item.currency}-${item.pricing_basis}`}
              className="rounded-xl border border-grid bg-page/20 p-3"
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <h3 className="text-xs font-semibold text-ink">{item.source}</h3>
                  <p className="text-[10px] uppercase tracking-wide text-muted">
                    {item.currency} · {humanize(item.pricing_basis)}
                  </p>
                </div>
                <Badge
                  tone={
                    item.coverage_pct == null
                      ? "info"
                      : item.coverage_pct >= 90
                        ? "good"
                        : "warning"
                  }
                >
                  {item.coverage_pct == null
                    ? "Unknown"
                    : `${Math.round(item.coverage_pct)}% exact`}
                </Badge>
              </div>
              <div className="mt-3">
                <CoverageMeter value={item.coverage_pct} />
              </div>
              <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
                <dt className="text-muted">Exact evidence</dt>
                <dd className="text-right tabular-nums text-ink-2">
                  {item.attributed_rows} / {item.total_rows} rows
                </dd>
                <dt className="text-muted">Exact cost</dt>
                <dd className="text-right tabular-nums text-ink-2">
                  {currency(item.attributed_cost, item.currency)}
                </dd>
                <dt className="text-muted">Outside attribution</dt>
                <dd className="text-right tabular-nums text-ink-2">
                  {currency(
                    item.total_cost - item.attributed_cost,
                    item.currency,
                  )}
                </dd>
              </dl>
            </article>
          ))}
        </div>
      )}

      <div className="mt-5 border-t border-grid pt-4">
        <h3 className="text-xs font-semibold text-ink">Outside the exact total</h3>
        <p className="mt-0.5 text-[11px] text-muted">
          Shared and unresolved costs remain visible without being claimed by the
          application.
        </p>
        {unallocated.length === 0 ? (
          <div className="mt-3">
            <EmptyState message="No shared, conflicting, or unattributed cost was reported." />
          </div>
        ) : (
          <ul className="mt-3 space-y-2" aria-label="Costs outside exact attribution">
            {unallocated.map((item, index) => (
              <li
                key={`${item.source}-${item.reason}-${index}`}
                className="flex min-w-0 flex-col gap-2 rounded-xl border border-grid bg-warning-surface/40 p-3 sm:flex-row sm:items-start sm:justify-between"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge tone="warning">Outside exact total</Badge>
                    <span className="text-xs font-medium text-ink">
                      {item.source}
                    </span>
                  </div>
                  <p className="mt-1 text-[11px] leading-4 text-muted">
                    {item.reason}
                  </p>
                </div>
                <div className="shrink-0 text-left sm:text-right">
                  <p className="text-sm font-semibold tabular-nums text-ink">
                    {currency(item.cost, item.currency)}
                  </p>
                  <p className="text-[10px] uppercase tracking-wide text-muted">
                    {humanize(item.pricing_basis)} · {item.row_count} rows
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

export function ApplicationTagAlignment({
  rows,
}: {
  rows: TagAlignment[];
}) {
  const counts = rows.reduce(
    (result, row) => {
      result[row.status] += 1;
      return result;
    },
    { matched: 0, missing: 0, conflict: 0 },
  );
  return (
    <Card>
      <SectionTitle
        title="Cross-cloud tag alignment"
        subtitle="Raw source tags are retained beside their normalized application identity"
        right={
          <div className="flex flex-wrap gap-1.5" aria-label="Tag status summary">
            <Badge tone="good">{counts.matched} matched</Badge>
            <Badge tone="warning">{counts.missing} missing</Badge>
            <Badge tone="serious">{counts.conflict} conflicts</Badge>
          </div>
        }
      />
      {rows.length === 0 ? (
        <EmptyState
          positive={false}
          message="No Azure or Databricks tag evidence has been collected."
        />
      ) : (
        <div
          role="region"
          aria-label="Scrollable cross-cloud tag alignment"
          tabIndex={0}
          className="overflow-x-auto rounded-xl border border-grid focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <table className="w-full min-w-[46rem] text-left text-xs">
            <caption className="sr-only">
              Tag values joined across Azure and Databricks
            </caption>
            <thead className="bg-hairline/40 text-muted">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">
                  Source / resource
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  Tag
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  Raw value
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  Normalized identity
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  Status
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr
                  key={`${row.source}-${row.resource_id ?? row.resource_name ?? index}-${row.tag_key ?? index}`}
                  className="border-t border-grid align-top"
                >
                  <td className="max-w-64 px-3 py-2">
                    <p className="font-medium text-ink">{row.source}</p>
                    <p
                      className="truncate text-[10px] text-muted"
                      title={row.resource_id ?? row.resource_name ?? undefined}
                    >
                      {row.resource_name ?? row.resource_id ?? "Source scope"}
                    </p>
                  </td>
                  <td className="px-3 py-2 font-mono text-[11px] text-ink-2">
                    {row.tag_key ?? "—"}
                  </td>
                  <td className="max-w-52 px-3 py-2">
                    <span className="break-words text-ink-2">
                      {row.raw_value ?? "—"}
                    </span>
                    {row.normalized_value &&
                      row.normalized_value !== row.raw_value && (
                        <p className="mt-0.5 text-[10px] text-muted">
                          normalized: {row.normalized_value}
                        </p>
                      )}
                  </td>
                  <td className="px-3 py-2 font-mono text-[11px] text-ink-2">
                    {row.normalized_value ?? "—"}
                  </td>
                  <td className="px-3 py-2">
                    <span className="inline-flex items-center gap-1.5">
                      {alignmentIcon(row.status)}
                      <Badge tone={alignmentTone(row.status)}>
                        {humanize(row.status)}
                      </Badge>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export function ApplicationDataHealth({
  sources,
}: {
  sources: ApplicationSourceHealth[];
}) {
  return (
    <Card>
      <SectionTitle
        title="Source health"
        subtitle="Coverage and freshness qualify every application cost claim"
      />
      {sources.some(
        (source) =>
          !["healthy", "available"].includes(source.status.toLowerCase()),
      ) && (
        <div
          role="status"
          className="mb-3 flex items-start gap-2 rounded-xl border border-warning-accent bg-warning-surface p-3 text-xs text-status-warning"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          One or more sources are partial, stale, or unavailable. Exact totals
          include only collected evidence.
        </div>
      )}
      <DataHealthList
        sources={sources.map((source) => ({
          source: source.source,
          status: source.status,
          freshness: source.last_success_at,
          notes: source.notes,
        }))}
      />
      {sources.some(
        (source) => source.coverage_start || source.coverage_end,
      ) && (
        <details className="mt-3 rounded-xl border border-grid px-3 py-2 text-xs text-muted">
          <summary className="cursor-pointer">Collection coverage</summary>
          <ul className="mt-2 space-y-1">
            {sources
              .filter(
                (source) => source.coverage_start || source.coverage_end,
              )
              .map((source) => (
                <li key={source.source}>
                  <span className="font-medium text-ink-2">{source.source}:</span>{" "}
                  <span>
                    {source.coverage_start ?? "unknown"} to{" "}
                    {source.coverage_end ?? "unknown"}
                  </span>
                </li>
              ))}
          </ul>
        </details>
      )}
    </Card>
  );
}

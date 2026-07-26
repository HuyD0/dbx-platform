import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Download, Search } from "lucide-react";
import { useId, useState } from "react";
import { apiGet } from "../../lib/api";
import { currency, dateTime } from "../../lib/format";
import type {
  ApplicationEnvelope,
  ApplicationEvidencePage,
  ApplicationWindow,
  AttributionMethod,
} from "../../types/applications";
import { AsOf, Badge, EmptyState, ErrorState, Skeleton } from "../ui";

const METHODS: Array<AttributionMethod | ""> = [
  "",
  "DIRECT_METADATA",
  "DIRECT_TAG",
  "DIRECT_RESOURCE",
  "CONFLICT",
  "SHARED_UNALLOCATED",
  "UNATTRIBUTED",
];

function humanize(value: string) {
  return value
    .toLowerCase()
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function methodTone(method: AttributionMethod) {
  if (
    method === "DIRECT_METADATA" ||
    method === "DIRECT_TAG" ||
    method === "DIRECT_RESOURCE"
  ) {
    return "good" as const;
  }
  if (method === "CONFLICT") return "serious" as const;
  return "warning" as const;
}

function evidenceUrl(
  applicationKey: string,
  days: ApplicationWindow,
  options: {
    cursor?: string;
    query?: string;
    source?: string;
    attributionMethod?: string;
    format?: string;
  },
) {
  const params = new URLSearchParams({
    window: `${days}d`,
    limit: "50",
  });
  if (options.cursor) params.set("cursor", options.cursor);
  if (options.query) params.set("q", options.query);
  if (options.source) params.set("source", options.source);
  if (options.attributionMethod) {
    params.set("attribution_method", options.attributionMethod);
  }
  if (options.format) params.set("format", options.format);
  return `/api/applications/${encodeURIComponent(
    applicationKey,
  )}/evidence?${params.toString()}`;
}

export function ApplicationEvidencePanel({
  applicationKey,
  days,
}: {
  applicationKey: string;
  days: ApplicationWindow;
}) {
  const searchId = useId();
  const sourceId = useId();
  const methodId = useId();
  const [opened, setOpened] = useState(false);
  const [queryText, setQueryText] = useState("");
  const [source, setSource] = useState("");
  const [attributionMethod, setAttributionMethod] = useState("");
  const [cursor, setCursor] = useState<string | undefined>();
  const [cursorHistory, setCursorHistory] = useState<Array<string | undefined>>(
    [],
  );
  const endpoint = evidenceUrl(applicationKey, days, {
    cursor,
    query: queryText,
    source,
    attributionMethod,
  });
  const query = useQuery({
    queryKey: [
      "/api/applications/evidence",
      applicationKey,
      days,
      cursor,
      queryText,
      source,
      attributionMethod,
    ],
    queryFn: () =>
      apiGet<ApplicationEnvelope<ApplicationEvidencePage>>(endpoint),
    enabled: opened,
    staleTime: 60_000,
    retry: false,
  });
  const resetCursor = () => {
    setCursor(undefined);
    setCursorHistory([]);
  };
  const exportHref = evidenceUrl(applicationKey, days, {
    query: queryText,
    source,
    attributionMethod,
    format: "csv",
  });

  return (
    <details
      className="glass rounded-2xl"
      onToggle={(event) => setOpened(event.currentTarget.open)}
    >
      <summary className="cursor-pointer list-none px-4 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-ink">Raw evidence</h2>
            <p className="mt-0.5 text-xs text-muted">
              Paginated billing, resource, tag, and binding evidence for audit
            </p>
          </div>
          <span className="text-xs font-medium text-accent">
            {opened ? "Hide evidence" : "Open evidence"}
          </span>
        </div>
      </summary>
      <div className="border-t border-grid p-4">
        <div className="mb-3 grid gap-2 md:grid-cols-[minmax(14rem,1fr)_11rem_13rem_auto]">
          <label
            htmlFor={searchId}
            className="flex items-center gap-2 rounded-xl border border-grid bg-page/40 px-3 py-2 text-xs focus-within:border-accent"
          >
            <Search className="h-3.5 w-3.5 shrink-0 text-muted" />
            <span className="sr-only">Search evidence</span>
            <input
              id={searchId}
              type="search"
              value={queryText}
              onChange={(event) => {
                setQueryText(event.target.value);
                resetCursor();
              }}
              placeholder="Resource, workload, or tag…"
              className="min-w-0 flex-1 bg-transparent text-ink outline-none placeholder:text-muted"
            />
          </label>
          <label htmlFor={sourceId} className="sr-only">
            Evidence source
          </label>
          <input
            id={sourceId}
            value={source}
            onChange={(event) => {
              setSource(event.target.value);
              resetCursor();
            }}
            placeholder="All sources"
            className="rounded-xl border border-grid bg-page/40 px-3 py-2 text-xs text-ink placeholder:text-muted"
          />
          <label htmlFor={methodId} className="sr-only">
            Attribution method
          </label>
          <select
            id={methodId}
            aria-label="Attribution method"
            value={attributionMethod}
            onChange={(event) => {
              setAttributionMethod(event.target.value);
              resetCursor();
            }}
            className="rounded-xl border border-grid bg-page/40 px-3 py-2 text-xs text-ink"
          >
            {METHODS.map((method) => (
              <option key={method || "all"} value={method}>
                {method ? humanize(method) : "All attribution methods"}
              </option>
            ))}
          </select>
          <a
            href={exportHref}
            download
            className="inline-flex items-center justify-center gap-1.5 rounded-xl border border-grid px-3 py-2 text-xs font-medium text-ink-2 hover:bg-hairline"
          >
            <Download className="h-3.5 w-3.5" />
            Export all CSV
          </a>
        </div>

        {query.isPending ? (
          <Skeleton rows={7} />
        ) : query.isError ? (
          <ErrorState error={query.error} />
        ) : (query.data?.data.items.length ?? 0) === 0 ? (
          <EmptyState
            positive={false}
            message="No evidence rows match the selected filters."
          />
        ) : (
          <>
            <div className="overflow-auto rounded-xl border border-grid">
              <table className="w-full min-w-[64rem] text-left text-xs">
                <caption className="sr-only">
                  Application cost attribution evidence
                </caption>
                <thead className="sticky top-0 bg-surface text-muted">
                  <tr>
                    <th scope="col" className="px-3 py-2 font-medium">
                      Date
                    </th>
                    <th scope="col" className="px-3 py-2 font-medium">
                      Source
                    </th>
                    <th scope="col" className="px-3 py-2 font-medium">
                      Resource / workload
                    </th>
                    <th scope="col" className="px-3 py-2 font-medium">
                      Attribution
                    </th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">
                      Amount
                    </th>
                    <th scope="col" className="px-3 py-2 font-medium">
                      Evidence
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {query.data?.data.items.map((item) => (
                    <tr
                      key={item.evidence_id}
                      className="border-t border-grid align-top"
                    >
                      <td className="whitespace-nowrap px-3 py-2 text-ink-2">
                        {item.usage_date}
                      </td>
                      <td className="px-3 py-2 text-ink-2">{item.source}</td>
                      <td className="max-w-80 px-3 py-2">
                        <p
                          className="truncate font-medium text-ink"
                          title={item.resource_id ?? undefined}
                        >
                          {item.resource_name ??
                            item.resource_id ??
                            item.workload ??
                            "Source scope"}
                        </p>
                        {item.workload && (
                          <p className="text-[10px] text-muted">{item.workload}</p>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <Badge tone={methodTone(item.attribution_method)}>
                          {humanize(item.attribution_method)}
                        </Badge>
                        {(item.tag_key || item.raw_application) && (
                          <p className="mt-1 font-mono text-[10px] text-muted">
                            {item.tag_key ?? "application"}=
                            {(item.tag_key
                              ? item.tags?.[item.tag_key]
                              : item.raw_application) ?? "—"}
                          </p>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-right">
                        <p className="font-medium tabular-nums text-ink">
                          {item.cost_known === false || item.inventory_only
                            ? "Not measured"
                            : currency(item.cost, item.currency)}
                        </p>
                        <p className="text-[10px] uppercase tracking-wide text-muted">
                          {item.evidence_kind
                            ? humanize(item.evidence_kind)
                            : humanize(item.pricing_basis)}
                        </p>
                      </td>
                      <td className="max-w-64 px-3 py-2">
                        <p className="text-ink-2">{dateTime(item.evidence_at)}</p>
                        <p className="truncate text-[10px] text-muted" title={item.scope}>
                          {item.scope}
                        </p>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
              <span>
                {query.data?.data.items.length ?? 0} evidence rows
                {query.data?.count != null ? ` · ${query.data.count} total` : ""}
              </span>
              <div className="flex items-center gap-2">
                <AsOf
                  asOf={query.data?.as_of}
                  cached={query.data?.cached}
                  onRefresh={() => query.refetch()}
                  refreshing={query.isFetching}
                />
                <button
                  type="button"
                  aria-label="Previous evidence page"
                  disabled={cursorHistory.length === 0}
                  onClick={() => {
                    const history = [...cursorHistory];
                    const previous = history.pop();
                    setCursor(previous);
                    setCursorHistory(history);
                  }}
                  className="rounded-lg border border-grid p-1.5 hover:bg-hairline disabled:opacity-30"
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  aria-label="Next evidence page"
                  disabled={!query.data?.data.next_cursor}
                  onClick={() => {
                    const next = query.data?.data.next_cursor;
                    if (!next) return;
                    setCursorHistory((history) => [...history, cursor]);
                    setCursor(next);
                  }}
                  className="rounded-lg border border-grid p-1.5 hover:bg-hairline disabled:opacity-30"
                >
                  <ChevronRight className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </details>
  );
}

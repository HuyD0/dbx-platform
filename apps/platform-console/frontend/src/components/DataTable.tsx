import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight, Download, Search } from "lucide-react";
import { useEffect, useId, useMemo, useState, type ReactNode } from "react";
import {
  columnLabel,
  currency,
  dateTime,
  duration,
  num,
  percent,
  severity,
} from "../lib/format";
import type { Row } from "../lib/types";
import { Badge, statusTone } from "./ui";

const MONEY_KEYS = /(?:_usd|cost|spend|savings)$/;
const DATE_KEYS = /(?:^|_)(?:date|time|timestamp|ts|at)$/;
const DURATION_KEYS = /duration_ms$/;
const PERCENT_KEYS = /(?:_pct|percent|percentage|rate)$/;
const STATUS_KEYS = /(?:^|_)(?:status|state|severity|risk)$/;

function CellValue({ column, value, row }: { column: string; value: unknown; row: Row }) {
  if (value === null || value === undefined || value === "") {
    return <span className="text-muted">—</span>;
  }
  if (column === "action") {
    return <Badge tone={severity(String(value))}>{String(value)}</Badge>;
  }
  if (column === "confidence") {
    const tone = String(value).toLowerCase() === "high" ? "good" : "info";
    return <Badge tone={tone}>{String(value)}</Badge>;
  }
  if (STATUS_KEYS.test(column)) {
    return <Badge tone={statusTone(value)}>{String(value).replaceAll("_", " ")}</Badge>;
  }
  if (MONEY_KEYS.test(column)) {
    return (
      <span className="whitespace-nowrap tabular-nums">
        {currency(
          value,
          String(row.currency ?? (column.endsWith("_usd") ? "USD" : "UNKNOWN")),
        )}
      </span>
    );
  }
  if (PERCENT_KEYS.test(column)) {
    return <span className="tabular-nums">{percent(value)}</span>;
  }
  if (DURATION_KEYS.test(column)) {
    return <span className="whitespace-nowrap tabular-nums">{duration(value)}</span>;
  }
  if (DATE_KEYS.test(column) && (typeof value === "string" || typeof value === "number")) {
    return <span className="whitespace-nowrap">{dateTime(value)}</span>;
  }
  if (typeof value === "number") {
    return <span className="tabular-nums">{num(value)}</span>;
  }
  if (typeof value === "boolean") {
    return <Badge tone={value ? "good" : "info"}>{value ? "yes" : "no"}</Badge>;
  }
  if (typeof value === "object") {
    return (
      <span className="line-clamp-3 break-words" title={JSON.stringify(value)}>
        {JSON.stringify(value)}
      </span>
    );
  }
  const text = String(value);
  return (
    <span className="break-words" title={text.length > 80 ? text : undefined}>
      {text}
    </span>
  );
}

function comparable(value: unknown): string | number {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return value;
  const numeric = Number(value);
  if (typeof value !== "boolean" && String(value).trim() !== "" && Number.isFinite(numeric)) {
    return numeric;
  }
  const timestamp = Date.parse(String(value));
  if (Number.isFinite(timestamp) && /\d{4}-\d{2}-\d{2}/.test(String(value))) return timestamp;
  return String(value).toLocaleLowerCase();
}

function csvValue(value: unknown): string {
  const rendered = value === null || value === undefined
    ? ""
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
  return `"${rendered.replaceAll('"', '""')}"`;
}

export function DataTable({
  rows,
  maxRows = 500,
  pageSize = 10,
  searchable = true,
  exportable = true,
  exportName = "dbx-platform-data",
  caption = "Report data",
  columns: requestedColumns,
  rowAction,
  rowActionLabel = "Actions",
}: {
  rows: Row[];
  maxRows?: number;
  pageSize?: number;
  searchable?: boolean;
  exportable?: boolean;
  exportName?: string;
  caption?: string;
  columns?: string[];
  rowAction?: (row: Row) => ReactNode;
  rowActionLabel?: string;
}) {
  const searchId = useId();
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ column: string; direction: "asc" | "desc" } | null>(null);
  const [page, setPage] = useState(0);
  const sourceRows = useMemo(() => rows.slice(0, maxRows), [rows, maxRows]);
  const columns = useMemo(() => {
    if (requestedColumns) return requestedColumns;
    const discovered = new Set<string>();
    sourceRows.slice(0, 100).forEach((row) =>
      Object.keys(row).forEach((column) => {
        if (!column.startsWith("_") && column !== "over_age") discovered.add(column);
      }),
    );
    return Array.from(discovered);
  }, [requestedColumns, sourceRows]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return sourceRows;
    return sourceRows.filter((row) =>
      columns.some((column) => {
        const value = row[column];
        const text = typeof value === "object" ? JSON.stringify(value) : String(value ?? "");
        return text.toLowerCase().includes(needle);
      }),
    );
  }, [columns, query, sourceRows]);

  const sorted = useMemo(() => {
    if (!sort) return filtered;
    return filtered
      .map((row, index) => ({ row, index }))
      .sort((a, b) => {
        const left = comparable(a.row[sort.column]);
        const right = comparable(b.row[sort.column]);
        const result =
          typeof left === "number" && typeof right === "number"
            ? left - right
            : String(left).localeCompare(String(right), undefined, {
                numeric: true,
                sensitivity: "base",
              });
        return (result || a.index - b.index) * (sort.direction === "asc" ? 1 : -1);
      })
      .map(({ row }) => row);
  }, [filtered, sort]);

  const pageCount = Math.max(1, Math.ceil(sorted.length / pageSize));
  const shown = sorted.slice(page * pageSize, (page + 1) * pageSize);

  useEffect(() => setPage(0), [query, sort, rows]);
  useEffect(() => {
    if (page >= pageCount) setPage(pageCount - 1);
  }, [page, pageCount]);

  if (rows.length === 0) return null;

  const toggleSort = (column: string) => {
    setSort((current) => {
      if (!current || current.column !== column) return { column, direction: "asc" };
      if (current.direction === "asc") return { column, direction: "desc" };
      return null;
    });
  };

  const exportCsv = () => {
    const lines = [
      columns.map(csvValue).join(","),
      ...sorted.map((row) => columns.map((column) => csvValue(row[column])).join(",")),
    ];
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${exportName}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div>
      {(searchable || exportable) && (
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          {searchable ? (
            <label
              htmlFor={searchId}
              className="flex min-w-52 flex-1 items-center gap-2 rounded-lg border border-grid bg-page/50 px-2.5 py-1.5 text-xs focus-within:border-accent"
            >
              <Search className="h-3.5 w-3.5 shrink-0 text-muted" />
              <span className="sr-only">Filter table</span>
              <input
                id={searchId}
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Filter this table…"
                className="w-full bg-transparent text-ink outline-none placeholder:text-muted"
              />
            </label>
          ) : (
            <span />
          )}
          {exportable && (
            <button
              type="button"
              onClick={exportCsv}
              className="inline-flex items-center gap-1.5 rounded-lg border border-grid px-2.5 py-1.5 text-xs font-medium text-ink-2 hover:bg-hairline"
            >
              <Download className="h-3.5 w-3.5" />
              Export CSV
            </button>
          )}
        </div>
      )}

      <div className="max-h-[34rem] overflow-auto rounded-lg border border-grid">
        <table className="w-full text-left text-xs">
          <caption className="sr-only">{caption}</caption>
          <thead className="sticky top-0 z-10 bg-surface/95 backdrop-blur-md">
            <tr className="border-b border-grid text-muted">
              {columns.map((column) => {
                const active = sort?.column === column;
                const SortIcon = active
                  ? sort.direction === "asc"
                    ? ArrowUp
                    : ArrowDown
                  : ArrowUpDown;
                return (
                  <th
                    key={column}
                    scope="col"
                    aria-sort={
                      active ? (sort.direction === "asc" ? "ascending" : "descending") : "none"
                    }
                    className="whitespace-nowrap p-0 font-medium"
                  >
                    <button
                      type="button"
                      onClick={() => toggleSort(column)}
                      className="inline-flex w-full items-center gap-1 px-2 py-2 text-left hover:bg-hairline hover:text-ink"
                    >
                      {columnLabel(column)}
                      <SortIcon className={`h-3 w-3 ${active ? "text-accent" : "opacity-50"}`} />
                    </button>
                  </th>
                );
              })}
              {rowAction && (
                <th scope="col" className="whitespace-nowrap px-2 py-2 font-medium">
                  {rowActionLabel}
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {shown.map((row, index) => (
              <tr
                key={`${page}-${index}`}
                className="border-b border-grid/60 align-top last:border-0 hover:bg-hairline/50"
              >
                {columns.map((column) => (
                  <td key={column} className="max-w-md px-2 py-2 text-ink-2">
                    <CellValue column={column} value={row[column]} row={row} />
                  </td>
                ))}
                {rowAction && (
                  <td className="whitespace-nowrap px-2 py-2">
                    {rowAction(row)}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        {shown.length === 0 && (
          <p className="p-5 text-center text-xs text-muted">No rows match “{query}”.</p>
        )}
      </div>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
        <span aria-live="polite">
          {sorted.length === 0
            ? "0 rows"
            : `${page * pageSize + 1}–${Math.min((page + 1) * pageSize, sorted.length)} of ${sorted.length}`}
          {rows.length > maxRows ? ` · limited to ${maxRows} of ${rows.length}` : ""}
        </span>
        {pageCount > 1 && (
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setPage((value) => Math.max(0, value - 1))}
              disabled={page === 0}
              aria-label="Previous page"
              className="rounded p-1 hover:bg-hairline disabled:opacity-30"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="min-w-16 text-center tabular-nums">
              Page {page + 1} of {pageCount}
            </span>
            <button
              type="button"
              onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))}
              disabled={page >= pageCount - 1}
              aria-label="Next page"
              className="rounded p-1 hover:bg-hairline disabled:opacity-30"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

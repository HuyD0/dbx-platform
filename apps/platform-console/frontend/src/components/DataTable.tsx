import { columnLabel, severity, usd } from "../lib/format";
import type { Row } from "../lib/types";
import { Badge } from "./ui";

const MONEY_KEYS = /(_usd|cost)$/;

function CellValue({ column, value }: { column: string; value: unknown }) {
  if (value === null || value === undefined || value === "") {
    return <span className="text-muted">—</span>;
  }
  if (column === "action") {
    return <Badge tone={severity(String(value))}>{String(value)}</Badge>;
  }
  if (column === "confidence") {
    const tone = value === "high" ? "good" : "info";
    return <Badge tone={tone}>{String(value)}</Badge>;
  }
  if (MONEY_KEYS.test(column)) {
    return <span className="tabular-nums">{usd(value)}</span>;
  }
  if (typeof value === "number") {
    return <span className="tabular-nums">{value.toLocaleString("en-US")}</span>;
  }
  return <>{String(value)}</>;
}

/** Renders finding/report rows. Columns come from the first row's keys —
 * rows originate in the dbx_platform package and stay schemaless. */
export function DataTable({ rows, maxRows = 100 }: { rows: Row[]; maxRows?: number }) {
  if (rows.length === 0) return null;
  const columns = Object.keys(rows[0]).filter((c) => !c.startsWith("_") && c !== "over_age");
  const shown = rows.slice(0, maxRows);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="border-b border-grid text-muted">
            {columns.map((c) => (
              <th key={c} className="whitespace-nowrap px-2 py-1.5 font-medium capitalize">
                {columnLabel(c)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((row, i) => (
            <tr key={i} className="border-b border-grid/60 align-top hover:bg-hairline/50">
              {columns.map((c) => (
                <td key={c} className="max-w-md px-2 py-1.5 text-ink-2">
                  <CellValue column={c} value={row[c]} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > maxRows && (
        <p className="mt-1 text-xs text-muted">
          Showing {maxRows} of {rows.length} rows.
        </p>
      )}
    </div>
  );
}

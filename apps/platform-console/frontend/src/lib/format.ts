export function usd(value: unknown): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: n >= 100 ? 0 : 2,
  });
}

export function num(value: unknown): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("en-US");
}

export function timeAgo(iso: string | number | null | undefined): string {
  if (iso === null || iso === undefined || iso === "" || iso === 0) return "—";
  const then = typeof iso === "number" ? iso : Date.parse(String(iso));
  if (!Number.isFinite(then)) return String(iso);
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

/** Severity tone for a finding's proposed action / reason keywords. */
export function severity(action: string): "critical" | "serious" | "warning" | "info" {
  const a = action.toLowerCase();
  if (a.includes("permanent-delete") || a.includes("revoke")) return "critical";
  if (a.includes("terminate") || a.includes("delete")) return "serious";
  if (a.includes("info")) return "info";
  return "warning";
}

/** Human column label from a snake_case/kebab-case key. */
export function columnLabel(key: string): string {
  return key.replace(/[_-]/g, " ").replace(/\b(usd|dbu|gpu|cpu|id|ts|pct)\b/gi, (m) =>
    m.toUpperCase(),
  );
}

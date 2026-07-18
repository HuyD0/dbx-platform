export function usd(value: unknown): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: n >= 100 ? 0 : 2,
  });
}

export function currency(value: unknown, code = "UNKNOWN"): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  if (!code || code.toUpperCase() === "UNKNOWN") {
    return `${n.toLocaleString("en-US", {
      maximumFractionDigits: n >= 100 ? 0 : n >= 1 ? 2 : 4,
    })} UNKNOWN`;
  }
  try {
    return n.toLocaleString("en-US", {
      style: "currency",
      currency: code,
      maximumFractionDigits: n >= 100 ? 0 : n >= 1 ? 2 : 4,
    });
  } catch {
    return `${n.toLocaleString("en-US")} ${code}`;
  }
}

export function num(value: unknown): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("en-US");
}

export function compactNum(value: unknown): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return new Intl.NumberFormat("en-US", {
    notation: Math.abs(n) >= 10_000 ? "compact" : "standard",
    maximumFractionDigits: 1,
  }).format(n);
}

export function percent(value: unknown): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const normalized = Math.abs(n) <= 1 ? n * 100 : n;
  return `${normalized.toLocaleString("en-US", { maximumFractionDigits: 1 })}%`;
}

export function dateTime(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  const d = new Date(String(value));
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: d.getFullYear() !== new Date().getFullYear() ? "numeric" : undefined,
    hour: "numeric",
    minute: "2-digit",
  });
}

export function duration(value: unknown): string {
  const milliseconds = Number(value);
  if (!Number.isFinite(milliseconds)) return "—";
  const seconds = Math.round(milliseconds / 1000);
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
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

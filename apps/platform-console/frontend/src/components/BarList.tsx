import { usd } from "../lib/format";

export interface BarDatum {
  label: string;
  value: number;
}

/** Ranked-magnitude horizontal bars: single sequential hue (magnitude is the
 * job, not identity), thin marks with rounded data-ends, direct labels —
 * value text in ink, never the series color. */
export function BarList({
  data,
  money = true,
  maxBars = 8,
}: {
  data: BarDatum[];
  money?: boolean;
  maxBars?: number;
}) {
  const shown = data.slice(0, maxBars);
  const max = Math.max(...shown.map((d) => d.value), 1);
  return (
    <div className="space-y-2">
      {shown.map((d) => (
        <div key={d.label} title={`${d.label}: ${money ? usd(d.value) : d.value}`}>
          <div className="mb-0.5 flex items-baseline justify-between gap-2 text-xs">
            <span className="truncate text-ink-2">{d.label}</span>
            <span className="shrink-0 tabular-nums text-ink">
              {money ? usd(d.value) : d.value.toLocaleString("en-US")}
            </span>
          </div>
          <div className="h-2 rounded-sm bg-hairline">
            <div
              className="h-2 rounded-sm bg-series-1"
              style={{ width: `${Math.max((d.value / max) * 100, 1)}%` }}
            />
          </div>
        </div>
      ))}
      {data.length > maxBars && (
        <p className="text-xs text-muted">and {data.length - maxBars} more…</p>
      )}
    </div>
  );
}

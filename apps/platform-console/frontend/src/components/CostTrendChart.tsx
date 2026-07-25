import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { currency } from "../lib/format";
import type { CostPoint } from "../lib/types";

const COLORS: Record<string, string> = {
  Databricks: "var(--color-series-1)",
  Compute: "var(--color-series-2)",
  Storage: "var(--color-series-3)",
  Network: "var(--color-series-4)",
  "AI / Foundry": "#8b6de8",
  Search: "#35a99b",
  Monitoring: "#d66f45",
  Other: "var(--color-muted)",
};

interface ChartRow {
  usage_date: string;
  total: number;
  [category: string]: number | string;
}

export function CostTrendChart({
  points,
  onSelectDate,
}: {
  points: CostPoint[];
  onSelectDate?: (usageDate: string) => void;
}) {
  const currencyCode = points[0]?.currency ?? "UNKNOWN";
  const categories = Array.from(new Set(points.map((point) => point.category)));
  const byDate = new Map<string, ChartRow>();
  points.forEach((point) => {
    const row = byDate.get(point.usage_date) ?? {
      usage_date: point.usage_date,
      total: 0,
    };
    row[point.category] = Number(row[point.category] ?? 0) + point.cost;
    row.total += point.cost;
    byDate.set(point.usage_date, row);
  });
  const data = Array.from(byDate.values()).sort((a, b) =>
    a.usage_date.localeCompare(b.usage_date),
  );

  if (data.length === 0) {
    return (
      <div className="grid h-64 place-items-center rounded-xl border border-dashed border-grid text-sm text-muted">
        No Azure actual-cost trend is available for this window.
      </div>
    );
  }

  return (
    <div
      className="h-72 w-full"
      role="img"
      aria-label={`Daily Azure actual cost in ${currencyCode}, stacked by service category`}
    >
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart
          data={data}
          margin={{ top: 8, right: 8, left: 2, bottom: 2 }}
          onClick={(state) => {
            const activeLabel = (state as { activeLabel?: string } | null)?.activeLabel;
            if (activeLabel && onSelectDate) onSelectDate(activeLabel);
          }}
          className={onSelectDate ? "cursor-pointer" : undefined}
        >
          <CartesianGrid stroke="var(--color-grid)" strokeDasharray="3 5" vertical={false} />
          <XAxis
            dataKey="usage_date"
            tick={{ fill: "var(--color-muted)", fontSize: 10 }}
            tickLine={false}
            axisLine={false}
            minTickGap={28}
            tickFormatter={(value) =>
              new Date(`${value}T00:00:00`).toLocaleDateString("en-US", {
                month: "short",
                day: "numeric",
              })
            }
          />
          <YAxis
            tick={{ fill: "var(--color-muted)", fontSize: 10 }}
            tickLine={false}
            axisLine={false}
            width={52}
            tickFormatter={(value) =>
              new Intl.NumberFormat("en-US", {
                notation: "compact",
                maximumFractionDigits: 1,
              }).format(Number(value))
            }
          />
          <Tooltip
            contentStyle={{
              background: "var(--color-surface)",
              border: "1px solid var(--color-grid)",
              borderRadius: "12px",
              color: "var(--color-ink)",
              fontSize: "12px",
            }}
            formatter={(value, name) => [currency(value, currencyCode), String(name)]}
            labelFormatter={(label) =>
              new Date(`${label}T00:00:00`).toLocaleDateString("en-US", {
                month: "short",
                day: "numeric",
                year: "numeric",
              })
            }
          />
          {categories.map((category) => (
            <Area
              key={category}
              type="monotone"
              dataKey={category}
              stackId="cost"
              stroke={COLORS[category] ?? COLORS.Other}
              fill={COLORS[category] ?? COLORS.Other}
              fillOpacity={0.24}
              strokeWidth={1.5}
              activeDot={{ r: 4 }}
            />
          ))}
          <Line
            type="monotone"
            dataKey="total"
            name="Total"
            stroke="var(--color-ink)"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

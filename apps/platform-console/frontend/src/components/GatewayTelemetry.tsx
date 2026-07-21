import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import { compactNum } from "../lib/format";
import type { Envelope, GatewayTelemetryRow } from "../lib/types";
import { AsOf, Card, EmptyState, ErrorState, SectionTitle, Skeleton } from "./ui";

const GATEWAY_TELEMETRY_PATH = "/api/performance/ai-gateway-telemetry";
const GOVERNED_GATEWAY_SOURCE = "system.ai_gateway.usage";
const RESPONSE_FRESH_FOR_MS = 10 * 60 * 1_000;
const SAMPLE_FRESH_FOR_MS = 48 * 60 * 60 * 1_000;

export interface GatewayTelemetryPoint {
  date: string;
  inputTokens: number;
  outputTokens: number;
  requests: number;
  p95LatencyMs: number | null;
}

function finiteNumber(value: unknown): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}

export function aggregateGatewayTelemetry(
  rows: GatewayTelemetryRow[],
): GatewayTelemetryPoint[] {
  const byDate = new Map<string, GatewayTelemetryPoint>();
  for (const row of rows) {
    const date = String(row.usage_date ?? "").slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) continue;
    const point = byDate.get(date) ?? {
      date,
      inputTokens: 0,
      outputTokens: 0,
      requests: 0,
      p95LatencyMs: null,
    };
    point.inputTokens += finiteNumber(row.input_tokens);
    point.outputTokens += finiteNumber(row.output_tokens);
    point.requests += finiteNumber(row.requests);
    const latency = finiteNumber(row.p95_latency_ms);
    if (latency > 0) {
      point.p95LatencyMs = Math.max(point.p95LatencyMs ?? 0, latency);
    }
    byDate.set(date, point);
  }
  return Array.from(byDate.values()).sort((a, b) => a.date.localeCompare(b.date));
}

function shortDate(value: string): string {
  const date = new Date(`${value}T00:00:00Z`);
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

export function gatewayTelemetryQueryKey(days: number) {
  return [GATEWAY_TELEMETRY_PATH, days] as const;
}

function fetchGatewayTelemetry(days: number, refresh = false) {
  return apiGet<Envelope<GatewayTelemetryRow[]>>(GATEWAY_TELEMETRY_PATH, {
    days,
    ...(refresh ? { refresh: true } : {}),
  });
}

function useGatewayTelemetry(days: number) {
  return useQuery({
    queryKey: gatewayTelemetryQueryKey(days),
    queryFn: () => fetchGatewayTelemetry(days),
    staleTime: 60_000,
    retry: false,
  });
}

type RatesStateKind = "live" | "stale" | "no-samples" | "unavailable";

interface RatesState {
  kind: RatesStateKind;
  label: string;
  title: string;
}

function sampleBucketEnd(value: unknown): number | null {
  const date = String(value ?? "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return null;
  const timestamp = Date.parse(`${date}T23:59:59.999Z`);
  if (!Number.isFinite(timestamp)) return null;
  return new Date(timestamp).toISOString().slice(0, 10) === date ? timestamp : null;
}

export function classifyLiveRates(
  response: Envelope<GatewayTelemetryRow[]>,
  now = Date.now(),
): RatesState {
  if (response.data.length === 0) {
    return {
      kind: "no-samples",
      label: "○ No rate samples",
      title: "No governed AI Gateway samples were returned for this window.",
    };
  }

  const observedAt = Date.parse(response.as_of);
  const samples = response.data
    .filter((row) => row.source === GOVERNED_GATEWAY_SOURCE)
    .map((row) => sampleBucketEnd(row.usage_date))
    .filter((timestamp): timestamp is number => timestamp !== null);
  if (!Number.isFinite(observedAt) || samples.length === 0) {
    return {
      kind: "unavailable",
      label: "× Rates unavailable",
      title: "Returned telemetry is missing a governed source or valid observation timestamp.",
    };
  }

  const latestSample = Math.max(...samples);
  if (now - observedAt > RESPONSE_FRESH_FOR_MS || now - latestSample > SAMPLE_FRESH_FOR_MS) {
    return {
      kind: "stale",
      label: "● Rates stale",
      title: `Latest governed sample: ${shortDate(new Date(latestSample).toISOString().slice(0, 10))}.`,
    };
  }
  return {
    kind: "live",
    label: "● Live Rates",
    title: `Latest governed sample: ${shortDate(new Date(latestSample).toISOString().slice(0, 10))}.`,
  };
}

const indicatorStyles: Record<RatesStateKind | "loading", string> = {
  live: "border-green-accent/50 bg-deep-maroon text-green-accent",
  stale: "border-gold-accent bg-light-gold text-deep-maroon",
  "no-samples": "border-sand-border bg-surface text-muted",
  unavailable: "border-primary-red/35 bg-critical-surface text-status-critical",
  loading: "border-sand-border bg-surface text-muted",
};

export function LiveRatesIndicator({ days = 30 }: { days?: number }) {
  const query = useGatewayTelemetry(days);
  const state: RatesState | { kind: "loading"; label: string; title: string } = query.isPending
    ? {
        kind: "loading",
        label: "◌ Checking rates",
        title: "Checking governed AI Gateway telemetry freshness.",
      }
    : query.isError
      ? {
          kind: "unavailable",
          label: "× Rates unavailable",
          title: "Governed AI Gateway telemetry could not be read.",
        }
      : classifyLiveRates(query.data);
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-bold ${indicatorStyles[state.kind]}`}
      role="status"
      aria-live="polite"
      aria-label={`Telemetry freshness: ${state.label.replace(/^[●○×◌]\s*/, "")}`}
      title={state.title}
    >
      {state.label}
    </span>
  );
}

function TokenBars({ points }: { points: GatewayTelemetryPoint[] }) {
  const maxTokens = Math.max(
    ...points.map((point) => point.inputTokens + point.outputTokens),
    1,
  );
  return (
    <figure aria-label="Daily AI Gateway input and output token throughput">
      <span className="sr-only">
        {points
          .map(
            (point) =>
              `${shortDate(point.date)}: ${point.inputTokens.toLocaleString("en-US")} input and ${point.outputTokens.toLocaleString("en-US")} output tokens`,
          )
          .join("; ")}
      </span>
      <div className="flex h-28 items-end gap-1.5" aria-hidden="true">
        {points.map((point) => {
          const total = point.inputTokens + point.outputTokens;
          const inputShare = total > 0 ? (point.inputTokens / total) * 100 : 0;
          return (
            <div
              key={point.date}
              className="relative flex min-w-1 flex-1 flex-col-reverse overflow-hidden rounded-t bg-hairline"
              style={{ height: `${Math.max(total > 0 ? 5 : 2, (total / maxTokens) * 100)}%` }}
              title={`${shortDate(point.date)}: ${total.toLocaleString("en-US")} tokens`}
            >
              {total > 0 && (
                <>
                  <span className="bg-teal-accent" style={{ height: `${inputShare}%` }} />
                  <span className="flex-1 bg-gold-accent" />
                </>
              )}
            </div>
          );
        })}
      </div>
      <figcaption className="mt-2 flex items-center justify-between text-[10px] text-muted">
        <span>{shortDate(points[0].date)}</span>
        <span>{shortDate(points[points.length - 1].date)}</span>
      </figcaption>
    </figure>
  );
}

function LatencyLine({ points }: { points: GatewayTelemetryPoint[] }) {
  const samples = points.filter(
    (point): point is GatewayTelemetryPoint & { p95LatencyMs: number } =>
      point.p95LatencyMs !== null,
  );
  const maxLatency = Math.max(...samples.map((point) => point.p95LatencyMs), 1);
  const positions = samples.map((point) => 8 + (1 - point.p95LatencyMs / maxLatency) * 78);
  return (
    <figure aria-label="Daily AI Gateway p95 latency trend">
      <span className="sr-only">
        {samples
          .map(
            (point) =>
              `${shortDate(point.date)}: ${Math.round(point.p95LatencyMs).toLocaleString("en-US")} milliseconds p95`,
          )
          .join("; ")}
      </span>
      <div className="relative flex h-28 overflow-hidden" aria-hidden="true">
        {[25, 50, 75].map((line) => (
          <span
            key={line}
            className="absolute inset-x-0 border-t border-grid/70"
            style={{ top: `${line}%` }}
          />
        ))}
        {samples.map((point, index) => {
          const top = positions[index];
          const previousTop = positions[index - 1];
          return (
            <span key={point.date} className="relative flex-1">
              {index > 0 && (
                <>
                  <span
                    className="absolute -left-1/2 h-0.5 w-full bg-gold-accent"
                    style={{ top: `${previousTop}%` }}
                  />
                  <span
                    className="absolute left-1/2 w-0.5 bg-gold-accent"
                    style={{
                      top: `${Math.min(previousTop, top)}%`,
                      height: `${Math.abs(previousTop - top)}%`,
                    }}
                  />
                </>
              )}
              <span
                className="absolute left-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface bg-teal-accent shadow-sm"
                style={{ top: `${top}%` }}
                title={`${shortDate(point.date)}: ${Math.round(point.p95LatencyMs)} ms p95`}
              />
            </span>
          );
        })}
      </div>
      <figcaption className="mt-2 flex items-center justify-between text-[10px] text-muted">
        <span>{shortDate(samples[0].date)}</span>
        <span>{shortDate(samples[samples.length - 1].date)}</span>
      </figcaption>
    </figure>
  );
}

export function GatewayTelemetry({ days = 30 }: { days?: number }) {
  const queryClient = useQueryClient();
  const queryKey = gatewayTelemetryQueryKey(days);
  const query = useGatewayTelemetry(days);
  const points = aggregateGatewayTelemetry(query.data?.data ?? []).slice(-14);
  const latest = points.at(-1);
  const latencyPoints = points.filter((point) => point.p95LatencyMs !== null);
  const latestLatency = latencyPoints.at(-1)?.p95LatencyMs ?? null;
  const totalTokens = points.reduce(
    (sum, point) => sum + point.inputTokens + point.outputTokens,
    0,
  );

  const content = (kind: "tokens" | "latency") => {
    if (query.isPending) return <Skeleton rows={4} />;
    if (query.isError) return <ErrorState error={query.error} />;
    if (points.length === 0) {
      return (
        <EmptyState
          positive={false}
          message={`No AI Gateway telemetry was persisted in the last ${days} days. Confirm Gateway traffic or run the governed ai-monitor job.`}
        />
      );
    }
    if (kind === "latency" && latencyPoints.length === 0) {
      return (
        <EmptyState
          positive={false}
          message="Gateway traffic was observed, but p95 latency was not reported."
        />
      );
    }
    return kind === "tokens" ? <TokenBars points={points} /> : <LatencyLine points={points} />;
  };

  return (
    <section className="grid gap-4 lg:grid-cols-12" aria-label="AI Gateway telemetry">
      <Card className="min-h-64 lg:col-span-7 xl:col-span-8">
        <SectionTitle
          title="AI Gateway token throughput"
          subtitle="Daily governed input and output token samples"
          right={
            <AsOf
              asOf={query.data?.as_of}
              cached={query.data?.cached}
              onRefresh={() =>
                queryClient.fetchQuery({
                  queryKey,
                  queryFn: () => fetchGatewayTelemetry(days, true),
                })
              }
              refreshing={query.isFetching}
            />
          }
        />
        <div className="mb-4 flex flex-wrap items-end justify-between gap-2">
          <div>
            <p className="text-3xl font-semibold tracking-tight text-ink">
              {latest ? compactNum(latest.inputTokens + latest.outputTokens) : "—"}
            </p>
            <p className="text-[11px] text-muted">tokens on latest observed day</p>
          </div>
          <div className="flex items-center gap-3 text-[11px] text-muted">
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-sm bg-teal-accent" /> input
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-sm bg-gold-accent" /> output
            </span>
            {totalTokens > 0 && <span>{compactNum(totalTokens)} shown</span>}
          </div>
        </div>
        {content("tokens")}
      </Card>

      <Card className="min-h-64 lg:col-span-5 xl:col-span-4">
        <SectionTitle
          title="AI Gateway latency"
          subtitle="Daily slowest endpoint p95, not an inferred SLA"
        />
        <div className="mb-4">
          <p className="text-3xl font-semibold tracking-tight text-ink">
            {latestLatency !== null ? Math.round(latestLatency).toLocaleString("en-US") : "—"}
            {latestLatency !== null && (
              <span className="ml-1 text-sm font-medium text-muted">ms p95</span>
            )}
          </p>
          <p className="text-[11px] text-muted">latest observed latency sample</p>
        </div>
        {content("latency")}
      </Card>
    </section>
  );
}

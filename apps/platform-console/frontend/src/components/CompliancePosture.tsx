import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertOctagon, CheckCircle2, ShieldAlert } from "lucide-react";
import { useId } from "react";
import { apiGet } from "../lib/api";
import type { AiCompliancePosture, ComplianceMetric, Envelope } from "../lib/types";
import { AsOf, Card, CapabilityNotice, SectionTitle, Skeleton } from "./ui";

const METRIC_ORDER: Array<ComplianceMetric["id"]> = [
  "zdr",
  "content_safety",
  "access_control",
  "audit_logging",
  "rate_limit_headroom",
];

const RADAR_LABELS: Record<ComplianceMetric["id"], string> = {
  zdr: "ZDR",
  content_safety: "Content safety",
  access_control: "Access control",
  audit_logging: "Audit logging",
  rate_limit_headroom: "Rate headroom",
};

function useComplianceQuery() {
  return useQuery({
    queryKey: ["/api/ai-governance/compliance"],
    queryFn: () => apiGet<Envelope<AiCompliancePosture>>("/api/ai-governance/compliance"),
    staleTime: 60_000,
    retry: false,
  });
}

function point(index: number, count: number, radius: number) {
  const angle = -Math.PI / 2 + (index * Math.PI * 2) / count;
  return {
    x: 130 + Math.cos(angle) * radius,
    y: 130 + Math.sin(angle) * radius,
  };
}

function polygonPoints(values: number[], radius: number) {
  return values
    .map((value, index) => {
      const position = point(index, values.length, radius * (value / 100));
      return `${position.x},${position.y}`;
    })
    .join(" ");
}

function metricTone(value: number | null) {
  if (value == null) return "text-muted";
  if (value >= 80) return "text-status-good";
  if (value >= 50) return "text-status-warning";
  return "text-status-critical";
}

function Radar({ metrics }: { metrics: ComplianceMetric[] }) {
  const titleId = useId();
  const descriptionId = useId();
  const byId = new Map(metrics.map((metric) => [metric.id, metric]));
  const ordered = METRIC_ORDER.map((id) => byId.get(id)).filter(
    (metric): metric is ComplianceMetric => Boolean(metric),
  );
  const values = ordered.map((metric) => metric.value_pct ?? 0);

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(18rem,1.1fr)_minmax(16rem,0.9fr)] lg:items-center">
      <svg
        viewBox="0 0 260 260"
        className="mx-auto aspect-square w-full max-w-[28rem] overflow-visible"
        role="img"
        aria-label="AI compliance radar chart"
        aria-describedby={descriptionId}
      >
        <title id={titleId}>AI compliance radar chart</title>
        <desc id={descriptionId}>
          Five control ratios for zero-data retention, content safety, access control, audit
          logging, and rate-limit headroom. Missing attestations plot at the center.
        </desc>
        {[25, 50, 75, 100].map((level) => (
          <polygon
            key={level}
            points={polygonPoints(new Array(ordered.length).fill(level), 78)}
            fill={level === 100 ? "var(--color-light-gold)" : "none"}
            fillOpacity={level === 100 ? 0.42 : undefined}
            stroke="var(--color-sand-border)"
            strokeWidth={level === 100 ? 1.5 : 1}
          />
        ))}
        {ordered.map((metric, index) => {
          const end = point(index, ordered.length, 78);
          return (
            <line
              key={metric.id}
              x1="130"
              y1="130"
              x2={end.x}
              y2={end.y}
              stroke="var(--color-sand-border)"
            />
          );
        })}
        <polygon
          points={polygonPoints(values, 78)}
          fill="var(--color-teal-accent)"
          fillOpacity="0.18"
          stroke="var(--color-teal-accent)"
          strokeWidth="2.5"
          strokeLinejoin="round"
        />
        {ordered.map((metric, index) => {
          const position = point(index, ordered.length, 78 * ((metric.value_pct ?? 0) / 100));
          const fill =
            metric.value_pct == null
              ? "var(--color-muted-rose-grey)"
              : metric.value_pct >= 80
                ? "var(--color-green-accent)"
                : metric.value_pct >= 50
                  ? "var(--color-gold-accent)"
                  : "var(--color-primary-red)";
          return (
            <circle
              key={metric.id}
              cx={position.x}
              cy={position.y}
              r="4"
              fill={fill}
              stroke="var(--color-surface)"
              strokeWidth="2"
            />
          );
        })}
        {ordered.map((metric, index) => {
          const label = point(index, ordered.length, 106);
          return (
            <text
              key={metric.id}
              x={label.x}
              y={label.y}
              textAnchor="middle"
              dominantBaseline="middle"
              fill="var(--color-brand-maroon)"
              className="text-[8px] font-semibold dark:fill-ink"
            >
              {RADAR_LABELS[metric.id]}
            </text>
          );
        })}
      </svg>

      <ul className="space-y-2" aria-label="Compliance metric details">
        {ordered.map((metric) => (
          <li
            key={metric.id}
            className="rounded-xl border border-sand-border bg-light-background/60 p-3"
          >
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-xs font-semibold text-deep-maroon dark:text-ink">
                {metric.label}
              </span>
              <strong className={`text-lg tabular-nums ${metricTone(metric.value_pct)}`}>
                {metric.value_pct == null ? "Not attested" : `${metric.value_pct}%`}
              </strong>
            </div>
            <p className="mt-1 text-[11px] leading-4 text-muted">
              {metric.evaluated_resources}/{metric.total_resources} resources evaluated ·{" "}
              {metric.evidence_note}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ComplianceRadarCard() {
  const queryClient = useQueryClient();
  const query = useComplianceQuery();
  const refresh = () =>
    queryClient.fetchQuery({
      queryKey: ["/api/ai-governance/compliance"],
      queryFn: () =>
        apiGet<Envelope<AiCompliancePosture>>("/api/ai-governance/compliance", {
          refresh: true,
        }),
    });

  return (
    <Card className="md:col-span-2">
      <SectionTitle
        title="AI compliance posture"
        subtitle="Evidence-backed controls across Databricks serving and Microsoft Foundry"
        right={
          <AsOf
            asOf={query.data?.as_of}
            cached={query.data?.cached}
            onRefresh={refresh}
            refreshing={query.isFetching}
          />
        }
      />
      {query.isPending ? (
        <Skeleton rows={5} />
      ) : query.isError ? (
        <CapabilityNotice
          title="Compliance evidence is not available"
          description="Run the AI catalog sync to attest ZDR, safety, access, audit, and capacity controls."
        />
      ) : (
        <Radar metrics={query.data.data.metrics} />
      )}
    </Card>
  );
}

function UnverifiedZdrNotice({ count }: { count: number }) {
  return (
    <div className="rounded-2xl border border-gold-accent bg-light-gold p-4" role="status">
      <div className="flex items-start gap-3">
        <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-status-warning" />
        <div>
          <h3 className="text-sm font-bold text-deep-maroon">
            ZDR requires evidence for {count} resource{count === 1 ? "" : "s"}
          </h3>
          <p className="mt-1 text-xs leading-5 text-ink-2">
            Add an explicit ZDR attestation to the resource inventory and rerun the catalog sync.
            Unverified resources do not count as compliant.
          </p>
        </div>
      </div>
    </div>
  );
}

export function ZdrEnforcer() {
  const query = useComplianceQuery();

  if (query.isPending) return <Skeleton rows={3} />;
  if (query.isError) {
    return (
      <CapabilityNotice
        title="ZDR enforcement evidence is unavailable"
        description="The catalog must attest each endpoint or workspace before ZDR can be claimed."
      />
    );
  }

  const posture = query.data.data;
  if (posture.evaluated_resources === 0) {
    return (
      <div className="rounded-2xl border border-gold-accent bg-light-gold p-4" role="status">
        <div className="flex items-start gap-3">
          <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-status-warning" />
          <div>
            <h3 className="text-sm font-bold text-deep-maroon">No ZDR posture is attested</h3>
            <p className="mt-1 text-xs leading-5 text-ink-2">
              Run the AI catalog sync and attach explicit ZDR evidence before treating any
              endpoint or workspace as compliant.
            </p>
          </div>
        </div>
      </div>
    );
  }
  if (posture.zdr_alerts.length === 0) {
    if (posture.unverified_zdr_resources > 0) {
      return <UnverifiedZdrNotice count={posture.unverified_zdr_resources} />;
    }
    return (
      <div className="rounded-2xl border border-green-accent bg-success-surface p-4" role="status">
        <div className="flex items-center gap-2 text-sm font-bold text-deep-maroon dark:text-ink">
          <CheckCircle2 className="h-5 w-5 text-green-accent" />
          ZDR is attested across every evaluated AI resource
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3" aria-label="Zero Data Retention enforcement alerts">
      {posture.unverified_zdr_resources > 0 && (
        <UnverifiedZdrNotice count={posture.unverified_zdr_resources} />
      )}
      {posture.zdr_alerts.map((alert) => (
        <article
          key={alert.resource_id}
          className="rounded-2xl border-2 border-primary-red bg-surface p-4"
          role="alert"
        >
          <div className="flex items-start gap-3">
            <span className="rounded-xl bg-critical-surface p-2 text-primary-red">
              <AlertOctagon className="h-5 w-5" />
            </span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-extrabold text-deep-maroon dark:text-ink">
                  ZDR disabled · {alert.resource_name}
                </h3>
                <span className="rounded-full border border-primary-red bg-critical-surface px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-status-critical">
                  Critical
                </span>
              </div>
              <p className="mt-1 text-xs text-muted">
                {alert.provider} {alert.scope} · {alert.resource_id}
              </p>
              <p className="mt-3 text-xs font-semibold text-mid-red">Required remediation</p>
              <p className="mt-1 text-xs leading-5 text-ink-2">{alert.remediation}</p>
              <p className="mt-2 text-[11px] leading-4 text-muted">
                This console remains read-only. Any routing or configuration change still needs
                an exact, expiring, single-use approved plan.
              </p>
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}

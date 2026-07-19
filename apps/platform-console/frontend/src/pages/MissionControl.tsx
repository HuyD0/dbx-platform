import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  Bot,
  CircleDollarSign,
  Gauge,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import { Link } from "react-router-dom";
import { PlanActionButton } from "../components/ActionPlanDialog";
import { BarList } from "../components/BarList";
import { DataTable } from "../components/DataTable";
import {
  AsOf,
  Badge,
  Card,
  DataHealthList,
  EmptyState,
  ErrorState,
  PageHeader,
  SectionTitle,
  Skeleton,
  StatTile,
  statusTone,
} from "../components/ui";
import { apiGet, isUnavailable } from "../lib/api";
import { useAssistantPanel } from "../lib/assistant-panel";
import { useChat } from "../lib/chat";
import { timeAgo, usd } from "../lib/format";
import type { Envelope, MissionControlData, OverviewData, PillarOutcome, Row } from "../lib/types";

interface MissionResult {
  envelope: Envelope<MissionControlData>;
  compatibility: boolean;
}

interface RawMissionControl {
  scope?: MissionControlData["scope"];
  outcomes?: {
    open_findings?: number;
    by_pillar?: Record<string, number>;
    by_severity?: Record<string, number>;
    awaiting_approval?: number;
  };
  top_decisions?: Row[];
  runtime?: Row;
  data_health?: Record<string, unknown>;
}

async function fetchMissionControl(): Promise<MissionResult> {
  try {
    const response = await apiGet<Envelope<MissionControlData> | RawMissionControl>(
      "/api/mission-control",
    );
    if ("data" in response) {
      return { envelope: response, compatibility: false };
    }
    const byPillar = Object.fromEntries(
      Object.entries(response.outcomes?.by_pillar ?? {}).map(([pillar, count]) => [
        pillar.toLowerCase(),
        count,
      ]),
    );
    const health = Object.entries(response.data_health ?? {}).map(([source, value]) => ({
      source: source.replaceAll("_", " "),
      status: String(value ?? "unknown"),
      notes: "Reported by the Mission Control repository",
    }));
    return {
      envelope: {
        data: {
          scope: response.scope,
          outcomes: Object.fromEntries(
            Object.entries(byPillar).map(([pillar, count]) => [
              pillar,
              {
                value: count,
                open_findings: count,
                status: Number(count) > 0 ? "attention" : "healthy",
              },
            ]),
          ),
          pending_approvals: response.outcomes?.awaiting_approval,
          decisions: response.top_decisions ?? [],
          data_health: health,
          findings: {
            data: {
              run_ts: null,
              total: response.outcomes?.open_findings ?? 0,
              by_area: byPillar,
              by_action: {},
            },
          },
        },
        count: null,
        as_of: "",
        cached: false,
      },
      compatibility: false,
    };
  } catch (error) {
    if (!isUnavailable(error)) throw error;
    const legacy = await apiGet<Envelope<OverviewData>>("/api/overview");
    return {
      envelope: {
        ...legacy,
        data: {
          findings: legacy.data.findings,
          spend: legacy.data.spend,
          digest: legacy.data.digest,
        },
      },
      compatibility: true,
    };
  }
}

const PILLARS = [
  {
    key: "cost",
    label: "Cost",
    href: "/cost",
    icon: CircleDollarSign,
    description: "Spend, budgets and unit economics",
  },
  {
    key: "security",
    label: "Security",
    href: "/security",
    icon: ShieldCheck,
    description: "Identity, access and policy risk",
  },
  {
    key: "risk",
    label: "Risk",
    href: "/security?tab=risk",
    icon: TriangleAlert,
    description: "Control drift and operational exposure",
  },
  {
    key: "performance",
    label: "Performance",
    href: "/performance",
    icon: Gauge,
    description: "SLO, reliability and efficiency",
  },
] as const;

const ACTION_TITLES: Record<string, string> = {
  "stale-clusters": "Review stale clusters",
  "orphaned-jobs": "Pause orphaned job schedules",
  "token-revoke": "Revoke over-age access tokens",
  "policy-sync": "Synchronize cluster policies",
};

const PLANNABLE_ACTIONS = new Set(Object.keys(ACTION_TITLES));

const DOMAIN_BASIS: Record<(typeof PILLARS)[number]["key"], string> = {
  cost: "Spend & efficiency",
  security: "Identity & policy",
  risk: "Control posture",
  performance: "SLO & reliability",
};

function humanize(value: string): string {
  const words = value
    .replace(/[._-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "";
}

function rowText(row: Row, ...keys: string[]): string | null {
  for (const key of keys) {
    const value = row[key];
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return null;
}

function affectedCount(row: Row): number {
  const affected = row.affected_resources;
  return Array.isArray(affected) ? affected.length : 0;
}

interface PriorityDecision {
  action: string | null;
  title: string;
  description: string;
  pillar: string;
  evidence: string;
  impact: string;
  control: string;
}

function normalizeDecision(row: Row): PriorityDecision {
  const rawAction = rowText(row, "proposed_action_type", "action");
  const action = rawAction && PLANNABLE_ACTIONS.has(rawAction) ? rawAction : null;
  const pillar = humanize(rowText(row, "pillar", "area") ?? "risk");
  const explicitTitle = rowText(row, "decision", "title", "recommendation");
  const check = rowText(row, "check_name", "check");
  const title =
    explicitTitle ??
    (rawAction ? ACTION_TITLES[rawAction] ?? humanize(rawAction) : null) ??
    (check ? `Review ${humanize(check).toLowerCase()}` : `Review ${pillar.toLowerCase()} evidence`);
  const evidenceValue = row.evidence;
  const evidenceFields =
    evidenceValue && typeof evidenceValue === "object" && !Array.isArray(evidenceValue)
      ? Object.keys(evidenceValue).length
      : 0;
  const targets = affectedCount(row);
  const evidence =
    rowText(row, "evidence") ??
    (targets > 0
      ? `${targets} affected resource${targets === 1 ? "" : "s"}`
      : evidenceFields > 0
        ? `${evidenceFields} evidence field${evidenceFields === 1 ? "" : "s"}`
        : check
          ? humanize(check)
          : "Canonical finding");
  const financialImpact = Number(row.financial_impact_usd ?? 0);
  const severity = humanize(rowText(row, "severity") ?? "reported");
  const impact =
    Number.isFinite(financialImpact) && financialImpact > 0
      ? `${usd(financialImpact)} exposure`
      : (rowText(row, "slo_impact") ??
        (rowText(row, "blast_radius") &&
        rowText(row, "blast_radius")?.toUpperCase() !== "UNKNOWN"
          ? `${humanize(rowText(row, "blast_radius") ?? "")} blast radius`
          : `${severity} severity`));
  const description =
    rowText(row, "reason", "description", "summary") ??
    `This ${pillar.toLowerCase()} finding is ranked highest by severity, impact, and evidence age.`;
  return {
    action,
    title,
    description,
    pillar,
    evidence,
    impact,
    control: action ? "Exact plan required" : "Human review required",
  };
}

function fallbackOutcome(
  key: string,
  data: MissionControlData,
  spendTotal: number,
): PillarOutcome {
  const byArea = data.findings?.data?.by_area ?? {};
  const areas: Record<string, string[]> = {
    cost: ["cost", "azure_cost", "forecast"],
    security: ["security"],
    risk: ["governance", "housekeeping", "ml"],
    performance: ["performance", "jobs", "sql"],
  };
  const findings = (areas[key] ?? []).reduce((total, area) => total + Number(byArea[area] ?? 0), 0);
  if (key === "cost") {
    return {
      value: spendTotal > 0 ? usd(spendTotal) : "No recent spend",
      status: findings > 0 ? "attention" : "healthy",
      open_findings: findings,
    };
  }
  return {
    value: findings,
    status: findings > 0 ? "attention" : "healthy",
    open_findings: findings,
  };
}

function fallbackDecisions(data: MissionControlData): Row[] {
  const byArea = data.findings?.data?.by_area ?? {};
  return Object.entries(byArea)
    .filter(([, count]) => Number(count) > 0)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .slice(0, 3)
    .map(([area, count], index) => ({
      priority: index + 1,
      decision: `Review ${area.replaceAll("_", " ")} findings`,
      pillar: area,
      evidence: `${count} open finding${Number(count) === 1 ? "" : "s"}`,
      confidence: "reported",
      approval: "required for changes",
    }));
}

export function MissionControl() {
  const openAssistant = useAssistantPanel();
  const { pending: assistantPending, send } = useChat();
  const query = useQuery({
    queryKey: ["mission-control"],
    queryFn: fetchMissionControl,
    staleTime: 60_000,
    retry: false,
  });

  if (query.isPending) {
    return (
      <div className="space-y-5">
        <PageHeader
          eyebrow="AI Mission Control"
          title="A decision-ready view of your platform"
          description="Cost, security, risk and performance—correlated into the next best decisions."
        />
        <Skeleton rows={8} />
      </div>
    );
  }
  if (query.isError) {
    return (
      <div className="space-y-5">
        <PageHeader
          eyebrow="AI Mission Control"
          title="A decision-ready view of your platform"
          description="Cost, security, risk and performance—correlated into the next best decisions."
        />
        <ErrorState error={query.error} />
      </div>
    );
  }

  const { envelope, compatibility } = query.data;
  const data = envelope.data;
  const findings = data.findings?.data;
  const spendRows = data.spend?.data ?? [];
  const spendTotal = spendRows.reduce((sum, row) => sum + Number(row.list_cost_usd ?? 0), 0);
  const decisions = data.decisions ?? fallbackDecisions(data);
  const outcomes = data.outcomes ?? {};
  const scope = data.scope;
  const sourceHealth = Array.isArray(data.data_health) ? data.data_health : [
    {
      source: compatibility ? "Legacy workspace summary" : "Mission Control",
      status: "healthy",
      freshness: envelope.as_of,
      notes: compatibility
        ? "Compatibility mode: normalized findings will replace area-level counts after migration."
        : null,
    },
  ];
  const healthySources = sourceHealth.filter(
    (source) => String(source.status).toLowerCase() === "healthy",
  ).length;
  const degradedSource = sourceHealth.find(
    (source) => String(source.status).toLowerCase() !== "healthy",
  );
  const priority = decisions.length > 0 ? normalizeDecision(decisions[0]) : null;
  const askAboutPriority = () => {
    if (!priority || assistantPending) return;
    openAssistant();
    send(
      `Why is "${priority.title}" the top priority for this workspace? Explain the evidence, ` +
        `impact, source freshness, and the exact approval control that would apply. Do not execute anything.`,
    );
  };

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="AI Mission Control"
        title="What needs a decision today?"
        description="AI investigates and correlates evidence continuously. You remain the control point for every change."
        actions={
          <AsOf
            asOf={envelope.as_of}
            cached={envelope.cached}
            onRefresh={() => query.refetch()}
            refreshing={query.isFetching}
          />
        }
      />

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge tone="info">{scope?.workspace_name ?? scope?.workspace ?? "Current workspace"}</Badge>
        <Badge tone="info">{scope?.environment ?? "development"}</Badge>
        {scope?.region && <Badge tone="info">{scope.region}</Badge>}
        {compatibility && <Badge tone="warning">compatibility mode</Badge>}
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <StatTile
          label="Open findings"
          value={findings?.total ?? "—"}
          tone={findings?.total ? "warning" : "good"}
          hint={
            decisions.length > 0
              ? `${decisions.length} ranked for review`
              : findings?.run_ts
                ? `collected ${timeAgo(findings.run_ts)}`
                : "awaiting normalized run"
          }
        />
        <StatTile
          label="Awaiting approval"
          value={data.pending_approvals ?? "—"}
          tone={data.pending_approvals ? "warning" : "default"}
          hint="No action executes without approval"
        />
        <StatTile
          label="Sources healthy"
          value={`${healthySources} / ${sourceHealth.length}`}
          tone={
            sourceHealth.length > 0 && healthySources === sourceHealth.length
              ? "good"
              : "warning"
          }
          hint={
            sourceHealth.length === 0
              ? "Coverage has not been reported"
              : degradedSource
                ? `${degradedSource.source}: ${String(degradedSource.status).replaceAll("_", " ")}`
                : "All reporting sources are healthy"
          }
        />
      </div>

      <section aria-labelledby="operational-posture-title">
        <div className="mb-3 flex flex-wrap items-start justify-between gap-3 border-t-2 border-grid pt-3">
          <div>
            <h2 id="operational-posture-title" className="text-base font-semibold text-ink">
              Operational posture
            </h2>
            <p className="mt-0.5 text-xs text-muted">
              Evidence stays grouped by domain; cross-domain priorities become decision briefs.
            </p>
          </div>
          <Badge tone="info">4 evidence domains</Badge>
        </div>

        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {PILLARS.map(({ key, label, href, icon: Icon, description }) => {
            const outcome = outcomes[key] ?? fallbackOutcome(key, data, spendTotal);
            const openFindings =
              outcome.open_findings ??
              (typeof outcome.value === "number" ? outcome.value : 0);
            const criticalFindings = Number(outcome.critical_findings ?? 0);
            const metric =
              typeof outcome.value === "number"
                ? `${outcome.value} open`
                : (outcome.value ?? `${openFindings} open`);
            return (
              <Link
                key={key}
                to={href}
                aria-label={`Open ${label}`}
                className="group rounded-2xl focus:outline-none"
              >
                <Card className="blueprint-domain-card flex h-full flex-col">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="rounded-lg border border-grid bg-page/50 p-1.5 text-accent">
                        <Icon className="h-4 w-4" />
                      </span>
                      <h3 className="text-sm font-semibold text-ink">{label}</h3>
                    </div>
                    <Badge tone={statusTone(outcome.status)}>{metric}</Badge>
                  </div>
                  <p className="mt-3 min-h-10 text-xs leading-5 text-muted">
                    {outcome.summary ?? description}
                  </p>
                  <div className="mt-auto flex items-center justify-between gap-3 border-t border-grid pt-3 text-[11px]">
                    <span className="font-medium text-ink-2">
                      {openFindings} finding{openFindings === 1 ? "" : "s"}
                    </span>
                    <span className="flex items-center gap-1 text-muted">
                      {criticalFindings > 0
                        ? `${criticalFindings} critical`
                        : DOMAIN_BASIS[key]}
                      <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
                    </span>
                  </div>
                </Card>
              </Link>
            );
          })}
        </div>

        {priority ? (
          <Card className="blueprint-priority mt-3">
            <div className="grid gap-5 xl:grid-cols-[minmax(0,1.05fr)_minmax(25rem,0.95fr)_auto] xl:items-center">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="info">Priority 01</Badge>
                  <Badge tone={statusTone(rowText(decisions[0], "severity"))}>
                    {priority.pillar}
                  </Badge>
                </div>
                <h3 className="mt-3 text-lg font-semibold tracking-tight text-ink">
                  {priority.title}
                </h3>
                <p className="mt-1.5 max-w-2xl text-xs leading-5 text-ink-2">
                  {priority.description}
                </p>
              </div>

              <dl className="grid gap-2 sm:grid-cols-3">
                {[
                  ["Evidence", priority.evidence],
                  ["Impact", priority.impact],
                  ["Control", priority.control],
                ].map(([label, value]) => (
                  <div key={label} className="border-t-2 border-grid pt-2">
                    <dt className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted">
                      {label}
                    </dt>
                    <dd className="mt-1 text-xs font-medium leading-5 text-ink">{value}</dd>
                  </div>
                ))}
              </dl>

              <div className="flex flex-col items-stretch gap-2 sm:flex-row xl:flex-col">
                {priority.action ? (
                  <PlanActionButton
                    action={priority.action}
                    label="Review exact plan"
                    tone="primary"
                  />
                ) : (
                  <Link
                    to="/actions"
                    className="rounded-lg border border-accent bg-accent px-3 py-1.5 text-center text-xs font-medium text-white hover:brightness-110"
                  >
                    Review in Action Center
                  </Link>
                )}
                <button
                  type="button"
                  onClick={askAboutPriority}
                  disabled={assistantPending}
                  className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-grid px-3 py-1.5 text-xs font-medium text-ink hover:bg-hairline disabled:opacity-50"
                >
                  <Bot className="h-3.5 w-3.5" />
                  Ask why this matters
                </button>
              </div>
            </div>
          </Card>
        ) : (
          <Card className="blueprint-priority mt-3">
            <EmptyState message="No cross-domain decision needs attention right now." />
          </Card>
        )}
      </section>

      <div className="grid gap-4 xl:grid-cols-[1.35fr_0.65fr]">
        <Card className="min-w-0">
          <SectionTitle
            title="Top decisions"
            subtitle="Deterministically ranked by critical impact, financial value and age"
            right={
              <Link to="/actions" className="text-xs font-medium text-accent hover:underline">
                Open Action Center
              </Link>
            }
          />
          {decisions.length > 0 ? (
            <DataTable
              rows={decisions}
              pageSize={3}
              searchable={false}
              exportable={false}
              caption="Top platform decisions"
            />
          ) : (
            <EmptyState message="No decision needs attention right now." />
          )}
        </Card>

        <Card className="min-w-0">
          <SectionTitle title="Data health" subtitle="Freshness and coverage are part of every answer" />
          <DataHealthList sources={sourceHealth} />
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="min-w-0">
          <SectionTitle title="Findings by domain" subtitle="Latest stored reporting cycle" />
          {findings && Object.keys(findings.by_area).length > 0 ? (
            <BarList
              money={false}
              data={Object.entries(findings.by_area).map(([label, value]) => ({ label, value }))}
            />
          ) : (
            <EmptyState message="No stored domain findings." />
          )}
        </Card>
        <Card className="min-w-0">
          <SectionTitle title="What changed" subtitle="New, resolved and materially changed since the prior run" />
          {(data.changes ?? []).length > 0 ? (
            <DataTable
              rows={data.changes ?? []}
              pageSize={5}
              searchable={false}
              exportName="platform-changes"
              caption="Recent platform changes"
            />
          ) : (
            <EmptyState
              message="Change history will appear after two normalized collection cycles."
              positive={false}
            />
          )}
        </Card>
      </div>

      {spendRows.length > 0 && (
        <Card className="min-w-0">
          <SectionTitle
            title="Databricks list cost"
            subtitle="Top reported SKUs only; open Cost & Value for complete basis and coverage"
            right={
              <Link to="/cost" className="inline-flex items-center gap-1 text-xs font-medium text-accent">
                Explore cost <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            }
          />
          <BarList
            maxBars={8}
            data={spendRows.map((row) => ({
              label: String(row.sku_name ?? "Unattributed SKU"),
              value: Number(row.list_cost_usd ?? 0),
            }))}
          />
        </Card>
      )}

      <p className="flex items-center gap-1.5 text-[11px] text-muted">
        <Sparkles className="h-3.5 w-3.5" />
        AI explanations may prioritize context, but executable plans are generated and validated
        deterministically.
      </p>
    </div>
  );
}

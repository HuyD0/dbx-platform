import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  CircleDollarSign,
  Gauge,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import { Link } from "react-router-dom";
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

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {PILLARS.map(({ key, label, href, icon: Icon, description }) => {
          const outcome = outcomes[key] ?? fallbackOutcome(key, data, spendTotal);
          return (
            <Link key={key} to={href} className="group rounded-2xl focus:outline-none">
              <Card className="h-full transition-transform group-hover:-translate-y-0.5 group-focus-visible:ring-2 group-focus-visible:ring-accent">
                <div className="flex items-start justify-between gap-3">
                  <span className="rounded-xl bg-accent/10 p-2 text-accent">
                    <Icon className="h-4 w-4" />
                  </span>
                  <Badge tone={statusTone(outcome.status)}>{outcome.status ?? "unknown"}</Badge>
                </div>
                <div className="mt-4 flex items-end justify-between gap-3">
                  <div>
                    <div className="text-xs text-muted">{label}</div>
                    <div className="mt-0.5 text-xl font-semibold text-ink">
                      {outcome.value ?? outcome.open_findings ?? "—"}
                    </div>
                  </div>
                  <ArrowRight className="h-4 w-4 text-muted transition-transform group-hover:translate-x-0.5" />
                </div>
                <p className="mt-2 text-[11px] text-muted">{outcome.summary ?? description}</p>
              </Card>
            </Link>
          );
        })}
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <StatTile
          label="Open findings"
          value={findings?.total ?? "—"}
          tone={findings?.total ? "warning" : "good"}
          hint={findings?.run_ts ? `collected ${timeAgo(findings.run_ts)}` : "awaiting normalized run"}
        />
        <StatTile
          label="Awaiting approval"
          value={data.pending_approvals ?? "—"}
          tone={data.pending_approvals ? "warning" : "default"}
          hint="No action executes without approval"
        />
        <StatTile
          label="Latest AI briefing"
          value={data.digest?.data?.latest_run_ts ? timeAgo(data.digest.data.latest_run_ts) : "none"}
          hint="Evidence-backed, read-only synthesis"
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.35fr_0.65fr]">
        <Card>
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

        <Card>
          <SectionTitle title="Data health" subtitle="Freshness and coverage are part of every answer" />
          <DataHealthList sources={sourceHealth} />
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
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
        <Card>
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
        <Card>
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

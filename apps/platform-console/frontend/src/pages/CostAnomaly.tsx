import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  ClipboardList,
  ShieldCheck,
} from "lucide-react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { CostTrendChart } from "../components/CostTrendChart";
import { DataTable } from "../components/DataTable";
import {
  AsOf,
  Badge,
  Card,
  ErrorState,
  PageHeader,
  SectionTitle,
  Skeleton,
  statusTone,
} from "../components/ui";
import { apiGet } from "../lib/api";
import { currency, percent } from "../lib/format";
import type { CostAnomalyDetail, Envelope } from "../lib/types";

export function CostAnomaly() {
  const { anomalyId = "" } = useParams();
  const [params] = useSearchParams();
  const days = Math.max(1, Math.min(365, Number(params.get("days") ?? 30)));
  const query = useQuery({
    queryKey: ["/api/cost/anomalies", anomalyId, days],
    queryFn: () =>
      apiGet<Envelope<CostAnomalyDetail>>(
        `/api/cost/anomalies/${encodeURIComponent(anomalyId)}?days=${days}`,
      ),
    staleTime: 60_000,
    retry: false,
  });

  if (query.isPending) {
    return (
      <div className="space-y-5">
        <Skeleton rows={12} />
      </div>
    );
  }
  if (query.isError) {
    return (
      <div className="space-y-5">
        <Link to="/" className="inline-flex items-center gap-1 text-xs text-accent">
          <ArrowLeft className="h-3.5 w-3.5" /> Cost Control
        </Link>
        <ErrorState error={query.error} />
      </div>
    );
  }

  const { anomaly, series, mover, scope, databricks_list: databricks } = query.data.data;
  const explorerUrl = `/cost?tab=categories&category=${encodeURIComponent(
    anomaly.category,
  )}&date=${anomaly.day}&days=${days}`;
  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Cost investigation"
        title={`${anomaly.category} · ${anomaly.signal}`}
        description={anomaly.reason}
        actions={
          <Link
            to="/"
            className="inline-flex items-center gap-1.5 rounded-lg border border-grid px-3 py-2 text-xs font-medium text-ink-2 hover:bg-hairline"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> Cost Control
          </Link>
        }
      />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={statusTone(anomaly.severity)}>{anomaly.severity}</Badge>
          <Badge tone="info">{anomaly.currency} · AZURE_ACTUAL</Badge>
          <span className="text-xs text-muted">{scope.environment}</span>
        </div>
        <AsOf
          asOf={query.data.as_of}
          cached={query.data.cached}
          onRefresh={() => query.refetch()}
          refreshing={query.isFetching}
        />
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <Card>
          <div className="text-xs font-medium text-muted">Observed spend</div>
          <div className="mt-1 text-2xl font-semibold tabular-nums text-ink">
            {currency(anomaly.cost, anomaly.currency)}
          </div>
          <p className="mt-1 text-xs text-muted">{anomaly.day}</p>
        </Card>
        <Card>
          <div className="text-xs font-medium text-muted">Expected baseline</div>
          <div className="mt-1 text-2xl font-semibold tabular-nums text-ink">
            {currency(anomaly.baseline, anomaly.currency)}
          </div>
          <p className="mt-1 text-xs text-muted">Comparable trailing period</p>
        </Card>
        <Card>
          <div className="text-xs font-medium text-muted">Variance</div>
          <div className="mt-1 text-2xl font-semibold tabular-nums text-status-serious">
            +{percent(anomaly.change_pct)}
          </div>
          <p className="mt-1 text-xs text-muted">Above the signal baseline</p>
        </Card>
        <Card>
          <div className="text-xs font-medium text-muted">Window movement</div>
          <div className="mt-1 text-2xl font-semibold tabular-nums text-ink">
            {mover?.change_pct == null ? "New spend" : percent(mover.change_pct)}
          </div>
          <p className="mt-1 text-xs text-muted">
            {mover ? currency(mover.change, mover.currency) : "No comparison"}
          </p>
        </Card>
      </div>

      <Card>
        <SectionTitle
          title={`${anomaly.category} before and after the signal`}
          subtitle="The highlighted investigation stays in the authoritative Azure actual-cost basis"
          right={
            <Link
              to={explorerUrl}
              className="inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline"
            >
              Open filtered Explorer <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          }
        />
        <CostTrendChart points={series} />
      </Card>

      <div className="grid gap-4 xl:grid-cols-[1.2fr_1fr]">
        <Card>
          <SectionTitle
            title="Investigation path"
            subtitle="Evidence first; any change remains governed and human-approved"
          />
          <ol className="space-y-3">
            {query.data.data.investigation.checks.map((check, index) => (
              <li key={check} className="flex gap-3 rounded-xl border border-grid p-3">
                <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-accent/12 text-xs font-semibold text-accent">
                  {index + 1}
                </span>
                <span className="text-sm leading-6 text-ink-2">{check}</span>
              </li>
            ))}
          </ol>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link
              to={explorerUrl}
              className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-2 text-xs font-medium text-white hover:opacity-90"
            >
              <ClipboardList className="h-3.5 w-3.5" /> Inspect evidence
            </Link>
            <Link
              to={`/actions?source=cost-anomaly&anomaly=${encodeURIComponent(anomaly.id)}`}
              className="inline-flex items-center gap-1.5 rounded-lg border border-grid px-3 py-2 text-xs font-medium text-ink-2 hover:bg-hairline"
            >
              <ShieldCheck className="h-3.5 w-3.5" /> Draft governed proposal
            </Link>
          </div>
        </Card>

        <Card>
          <SectionTitle
            title="Databricks attribution context"
            subtitle="Non-additive workload signal for explaining the Azure Databricks bill"
          />
          <div className="rounded-xl border border-grid bg-hairline/20 p-3">
            <div className="text-2xl font-semibold tabular-nums text-ink">
              {currency(databricks.cost, databricks.currency)}
            </div>
            <div className="mt-1 text-xs text-muted">{databricks.cost_basis}</div>
            <p className="mt-3 text-xs leading-5 text-ink-2">
              {databricks.notes} This amount is not added to the Azure total.
            </p>
          </div>
          <div className="mt-3 flex items-center gap-2 text-xs text-status-good">
            <CheckCircle2 className="h-4 w-4" />
            Cost bases remain separate
          </div>
        </Card>
      </div>

      {databricks.rows.length > 0 && (
        <Card>
          <SectionTitle
            title="Workspace SKU evidence"
            subtitle="Use this attribution to identify the workload behind a Databricks-category signal"
          />
          <DataTable
            rows={databricks.rows}
            caption="Databricks workload evidence"
            exportName={`cost-anomaly-${anomaly.day}`}
          />
        </Card>
      )}
    </div>
  );
}

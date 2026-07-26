import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Clock3, Tags } from "lucide-react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  ApplicationCoveragePanel,
  ApplicationDataHealth,
  ApplicationTagAlignment,
} from "../components/applications/ApplicationAttributionViews";
import {
  APPLICATION_WINDOWS,
  ApplicationWindowPicker,
} from "../components/applications/ApplicationControls";
import { ApplicationEvidencePanel } from "../components/applications/ApplicationEvidencePanel";
import {
  ApplicationDrivers,
  ApplicationLedgerCards,
  ApplicationTrends,
} from "../components/applications/ApplicationLedgerViews";
import {
  AsOf,
  Badge,
  Card,
  ErrorState,
  PageHeader,
  SectionTitle,
  Skeleton,
} from "../components/ui";
import { apiGet } from "../lib/api";
import { timeAgo } from "../lib/format";
import type {
  ApplicationEnvelope,
  ApplicationProfile,
  ApplicationWindow,
} from "../types/applications";

function requestedWindow(value: string | null): ApplicationWindow {
  const parsed = Number(value?.replace("d", "") ?? 30);
  return APPLICATION_WINDOWS.includes(parsed as ApplicationWindow)
    ? (parsed as ApplicationWindow)
    : 30;
}

export function ApplicationDetail() {
  const { applicationKey = "" } = useParams<{ applicationKey: string }>();
  const [params, setParams] = useSearchParams();
  const days = requestedWindow(params.get("window"));
  const query = useQuery({
    queryKey: ["/api/applications/detail", applicationKey, days],
    queryFn: () =>
      apiGet<ApplicationEnvelope<ApplicationProfile>>(
        `/api/applications/${encodeURIComponent(applicationKey)}`,
        { window: `${days}d` },
      ),
    enabled: Boolean(applicationKey),
    staleTime: 60_000,
    retry: false,
  });

  const header = (
    <div className="space-y-3">
      <Link
        to={`/applications${days === 30 ? "" : `?window=${days}d`}`}
        className="inline-flex items-center gap-1 text-xs font-medium text-muted hover:text-accent"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        All applications
      </Link>
      <PageHeader
        eyebrow="Application FinOps"
        title={query.data?.data.application.display_name ?? "Application"}
        description="Exact attributed cost, source-native ledgers, cross-cloud tag evidence, and costs intentionally kept outside the total."
        actions={
          <ApplicationWindowPicker
            value={days}
            onChange={(window) => {
              const next = new URLSearchParams(params);
              if (window === 30) next.delete("window");
              else next.set("window", `${window}d`);
              setParams(next, { replace: true });
            }}
          />
        }
      />
    </div>
  );

  if (!applicationKey) {
    return (
      <div className="space-y-5">
        {header}
        <Card>
          <p className="text-sm text-muted">
            No application key was provided.
          </p>
        </Card>
      </div>
    );
  }

  if (query.isPending) {
    return (
      <div className="space-y-5">
        {header}
        <Skeleton rows={14} />
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="space-y-5">
        {header}
        <ErrorState error={query.error} />
      </div>
    );
  }

  const { data, as_of: asOf, cached } = query.data;
  const application = data.application;
  const hasUntrustedLedger = data.ledgers.some(
    (ledger) => ledger.trusted === false,
  );

  return (
    <div className="space-y-5">
      {header}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
          {application.environments.map((environment) => (
            <Badge key={environment} tone="info">
              {environment}
            </Badge>
          ))}
          {application.sources.map((source) => (
            <span
              key={source}
              className="rounded-full border border-grid px-2 py-0.5"
            >
              {source}
            </span>
          ))}
          {application.last_evidence_at && (
            <span className="inline-flex items-center gap-1">
              <Clock3 className="h-3.5 w-3.5" />
              evidence {timeAgo(application.last_evidence_at)}
            </span>
          )}
        </div>
        <AsOf
          asOf={asOf}
          cached={cached}
          onRefresh={() => query.refetch()}
          refreshing={query.isFetching}
        />
      </div>

      <Card>
        <SectionTitle
          title={
            hasUntrustedLedger
              ? "Attributed cost evidence"
              : "Exact attributed cost"
          }
          subtitle={`${data.period.start ?? "Unknown start"} to ${
            data.period.end ?? "unknown end"
          } · ${
            hasUntrustedLedger
              ? "stale, partial, or unavailable source health is excluded from a current exact claim"
              : "only direct metadata, tag, or resource-binding evidence"
          }`}
        />
        <ApplicationLedgerCards ledgers={data.ledgers} />
      </Card>

      <ApplicationTrends ledgers={data.ledgers} points={data.series} />

      <div className="grid gap-4 xl:grid-cols-[1.15fr_0.85fr]">
        <ApplicationDrivers ledgers={data.ledgers} drivers={data.drivers} />
        <ApplicationCoveragePanel
          coverage={data.coverage}
          unallocated={data.unallocated}
        />
      </div>

      <ApplicationTagAlignment rows={data.tag_alignment} />

      <ApplicationDataHealth sources={data.source_health} />

      <ApplicationEvidencePanel applicationKey={applicationKey} days={days} />

      <div className="flex items-start gap-2 rounded-xl border border-grid bg-hairline/20 p-3 text-xs leading-5 text-muted">
        <Tags className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
        Missing or conflicting tags are recommendations only. This view never
        changes Azure or Databricks resources.
      </div>
    </div>
  );
}

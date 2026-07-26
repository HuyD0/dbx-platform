import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  LayoutGrid,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  ApplicationFilters,
  APPLICATION_WINDOWS,
  ApplicationWindowPicker,
} from "../components/applications/ApplicationControls";
import { ApplicationPortfolioCard } from "../components/applications/ApplicationPortfolioCard";
import {
  AsOf,
  Badge,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  Skeleton,
  statusTone,
} from "../components/ui";
import { apiGet } from "../lib/api";
import type {
  ApplicationEnvelope,
  ApplicationPortfolio,
  ApplicationWindow,
} from "../types/applications";

function requestedWindow(value: string | null): ApplicationWindow {
  const parsed = Number(value?.replace("d", "") ?? 30);
  return APPLICATION_WINDOWS.includes(parsed as ApplicationWindow)
    ? (parsed as ApplicationWindow)
    : 30;
}

export function Applications() {
  const [params, setParams] = useSearchParams();
  const days = requestedWindow(params.get("window"));
  const search = params.get("q") ?? "";
  const environment = params.get("environment") ?? "";
  const source = params.get("source") ?? "";
  const cursor = params.get("cursor") ?? undefined;
  const [cursorHistory, setCursorHistory] = useState<Array<string | undefined>>(
    [],
  );
  const query = useQuery({
    queryKey: [
      "/api/applications",
      days,
      search,
      environment,
      source,
      cursor,
    ],
    queryFn: () =>
      apiGet<ApplicationEnvelope<ApplicationPortfolio>>("/api/applications", {
        window: `${days}d`,
        q: search,
        environment,
        source,
        cursor: cursor ?? "",
        limit: 24,
      }),
    staleTime: 60_000,
    retry: false,
  });

  useEffect(() => {
    setCursorHistory([]);
  }, [days, search, environment, source]);

  const updateFilter = (
    key: "q" | "environment" | "source",
    value: string,
  ) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("cursor");
    setParams(next, { replace: true });
  };

  const header = (
    <PageHeader
      eyebrow="Application FinOps"
      title="Applications"
      description="See each application’s attributable Azure and Databricks cost, cross-cloud tags, and evidence coverage without blending currencies or pricing bases."
      actions={
        <ApplicationWindowPicker
          value={days}
          onChange={(window) => {
            const next = new URLSearchParams(params);
            if (window === 30) next.delete("window");
            else next.set("window", `${window}d`);
            next.delete("cursor");
            setParams(next, { replace: true });
          }}
        />
      }
    />
  );

  if (query.isPending) {
    return (
      <div className="space-y-5">
        {header}
        <Card>
          <Skeleton rows={3} />
        </Card>
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <Skeleton rows={8} />
          </Card>
          <Card>
            <Skeleton rows={8} />
          </Card>
        </div>
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

  const { data, as_of: asOf, cached, count, source_status: sourceStatus } =
    query.data;
  const sourceDegraded =
    sourceStatus &&
    !["healthy", "available"].includes(sourceStatus.status.toLowerCase());

  return (
    <div className="space-y-5">
      {header}

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-xs text-muted">
            <LayoutGrid className="h-4 w-4 text-accent" />
            <span>
              {count ?? data.applications.length} application
              {(count ?? data.applications.length) === 1 ? "" : "s"} in scope
            </span>
            <Badge tone="info">{days} days</Badge>
          </div>
          <AsOf
            asOf={asOf}
            cached={cached}
            onRefresh={() => query.refetch()}
            refreshing={query.isFetching}
          />
        </div>
        <div className="mt-4">
          <ApplicationFilters
            query={search}
            environment={environment}
            source={source}
            facets={data.facets}
            onQueryChange={(value) => updateFilter("q", value)}
            onEnvironmentChange={(value) =>
              updateFilter("environment", value)
            }
            onSourceChange={(value) => updateFilter("source", value)}
          />
        </div>
      </Card>

      {sourceDegraded && (
        <div
          role="status"
          className="flex items-start gap-2 rounded-xl border border-warning-accent bg-warning-surface p-3 text-xs text-status-warning"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <span className="font-medium">
              {sourceStatus.status.replaceAll("_", " ")}
            </span>
            {sourceStatus.notes ? ` · ${sourceStatus.notes}` : ""}
          </div>
        </div>
      )}

      {data.applications.length === 0 ? (
        <Card>
          <EmptyState
            positive={false}
            message={
              search || environment || source
                ? "No applications match these filters."
                : "No application identity evidence has been collected."
            }
          />
        </Card>
      ) : (
        <section
          className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3"
          aria-label="Application portfolio"
        >
          {data.applications.map((application) => (
            <ApplicationPortfolioCard
              key={application.application_key}
              summary={application}
              window={days}
            />
          ))}
        </section>
      )}

      {(cursor || data.next_cursor) && (
        <nav
          aria-label="Application pages"
          className="flex items-center justify-between gap-3 text-xs text-muted"
        >
          <button
            type="button"
            disabled={!cursor}
            onClick={() => {
              const history = [...cursorHistory];
              const previous = history.pop();
              const next = new URLSearchParams(params);
              if (previous) next.set("cursor", previous);
              else next.delete("cursor");
              setCursorHistory(history);
              setParams(next);
            }}
            className="inline-flex items-center gap-1 rounded-lg border border-grid px-2.5 py-1.5 font-medium text-ink-2 hover:bg-hairline disabled:opacity-30"
          >
            <ChevronLeft className="h-3.5 w-3.5" /> Previous
          </button>
          <span>
            Server-filtered results ·{" "}
            <Badge tone={statusTone(sourceStatus?.status ?? "healthy")}>
              {sourceStatus?.status ?? "current"}
            </Badge>
          </span>
          <button
            type="button"
            disabled={!data.next_cursor}
            onClick={() => {
              if (!data.next_cursor) return;
              const next = new URLSearchParams(params);
              setCursorHistory((history) => [...history, cursor]);
              next.set("cursor", data.next_cursor);
              setParams(next);
            }}
            className="inline-flex items-center gap-1 rounded-lg border border-grid px-2.5 py-1.5 font-medium text-ink-2 hover:bg-hairline disabled:opacity-30"
          >
            Next <ChevronRight className="h-3.5 w-3.5" />
          </button>
        </nav>
      )}
    </div>
  );
}

import { useMutation, useQuery } from "@tanstack/react-query";
import { Play } from "lucide-react";
import { useState } from "react";
import { apiGet, apiPost } from "../lib/api";
import { timeAgo } from "../lib/format";
import type { Envelope, JobInfo, RunInfo } from "../lib/types";
import { AsOf, Badge, Card, EmptyState, ErrorState, SectionTitle, Skeleton } from "../components/ui";

function RunHistory({ jobId }: { jobId: number }) {
  const query = useQuery({
    queryKey: ["runs", jobId],
    queryFn: () => apiGet<{ data: RunInfo[] }>(`/api/jobs/${jobId}/runs`),
    retry: false,
  });
  if (query.isPending) return <Skeleton rows={2} />;
  if (query.isError) return <ErrorState error={query.error} />;
  if (query.data.data.length === 0) return <p className="text-xs text-muted">No runs yet.</p>;
  return (
    <ul className="space-y-1 text-xs">
      {query.data.data.map((r) => (
        <li key={r.run_id} className="flex items-center gap-2 text-ink-2">
          <Badge
            tone={
              r.result === "SUCCESS" ? "good" : r.result === "" ? "info" : "critical"
            }
          >
            {r.result || r.state || "PENDING"}
          </Badge>
          <span className="tabular-nums">#{r.run_id}</span>
          <span className="text-muted">{timeAgo(r.started_ms)}</span>
          {r.duration_ms != null && (
            <span className="text-muted">· {Math.round(r.duration_ms / 1000)}s</span>
          )}
        </li>
      ))}
    </ul>
  );
}

export function Jobs() {
  const query = useQuery({
    queryKey: ["jobs"],
    queryFn: () => apiGet<Envelope<JobInfo[]>>("/api/jobs"),
    staleTime: 60_000,
    retry: false,
  });
  const [expanded, setExpanded] = useState<number | null>(null);
  const [started, setStarted] = useState<Record<number, number>>({});
  const run = useMutation({
    mutationFn: (jobId: number) => apiPost<{ run_id: number }>(`/api/jobs/${jobId}/run_now`),
    onSuccess: (d, jobId) => setStarted((s) => ({ ...s, [jobId]: d.run_id })),
  });

  return (
    <div className="space-y-4">
      <Card>
        <SectionTitle
          title="Report jobs"
          subtitle="The bundle's scheduled [dbx-platform] jobs — report-only by definition, safe to kick off early"
          right={
            query.data && (
              <AsOf
                asOf={query.data.as_of}
                cached={query.data.cached}
                onRefresh={() => query.refetch()}
                refreshing={query.isFetching}
              />
            )
          }
        />
        {query.isPending ? (
          <Skeleton rows={5} />
        ) : query.isError ? (
          <ErrorState error={query.error} />
        ) : query.data.data.length === 0 ? (
          <EmptyState message="No [dbx-platform] jobs visible — deploy the bundle and grant the app CAN_MANAGE_RUN (docs/runbook.md)." />
        ) : (
          <ul className="divide-y divide-grid">
            {query.data.data.map((job) => (
              <li key={job.job_id} className="py-2">
                <div className="flex items-center justify-between gap-2">
                  <button
                    type="button"
                    className="truncate text-left text-sm text-ink hover:underline"
                    onClick={() =>
                      setExpanded(expanded === job.job_id ? null : job.job_id)
                    }
                    aria-expanded={expanded === job.job_id}
                  >
                    {job.name}
                  </button>
                  <div className="flex shrink-0 items-center gap-2">
                    {started[job.job_id] && (
                      <Badge tone="good">started run {started[job.job_id]}</Badge>
                    )}
                    <button
                      type="button"
                      onClick={() => run.mutate(job.job_id)}
                      disabled={run.isPending}
                      className="inline-flex items-center gap-1 rounded-lg border border-grid px-2.5 py-1 text-xs font-medium text-ink hover:bg-hairline disabled:opacity-50"
                    >
                      <Play className="h-3 w-3" />
                      Run now
                    </button>
                  </div>
                </div>
                {expanded === job.job_id && (
                  <div className="mt-2 pl-1">
                    <RunHistory jobId={job.job_id} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
        {run.isError && (
          <div className="mt-2">
            <ErrorState error={run.error} />
          </div>
        )}
      </Card>
    </div>
  );
}

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ActionPlanDialog } from "../components/ActionPlanDialog";
import { apiGet } from "../lib/api";
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
          {r.state_message && (
            <span className="min-w-0 truncate text-muted" title={r.state_message}>
              · {r.state_message}
            </span>
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
  const [plannedJob, setPlannedJob] = useState<JobInfo | null>(null);
  const allScheduledPaused =
    query.data?.data.some((job) => job.schedule_type === "CRON") &&
    query.data.data
      .filter((job) => job.schedule_type === "CRON")
      .every((job) => job.schedule_status === "PAUSED");

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-2 rounded-xl border border-status-warning/30 bg-status-warning/5 p-3 text-xs leading-5 text-ink-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-status-warning" />
        <div>
          Scheduled runs are autonomous when their schedule is active. For an on-demand run,
          use <strong className="font-semibold text-ink">Plan run</strong> below. Databricks
          <strong className="font-semibold text-ink"> Run now</strong> and repair launches do
          not carry an approved action ID and are rejected intentionally.
          {allScheduledPaused && (
            <span>
              {" "}All managed schedules are paused; use{" "}
              <Link className="font-semibold text-accent hover:underline" to="/runtime">
                Resources & Runtime → Plan wake
              </Link>{" "}
              to resume the previously active schedules.
            </span>
          )}
        </div>
      </div>
      <Card>
        <SectionTitle
          title="Jobs & schedules"
          subtitle="Owned schedules, current pause state and governed run history"
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
          <EmptyState message="No bundle-owned jobs are visible — deploy the bundle and grant the app CAN_VIEW on the exact job IDs (docs/runbook.md)." />
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
                    <Badge
                      tone={
                        job.schedule_status === "UNPAUSED"
                          ? "good"
                          : job.schedule_status === "PAUSED"
                            ? "warning"
                            : "info"
                      }
                    >
                      {job.schedule_status === "UNPAUSED"
                        ? "scheduled"
                        : job.schedule_status === "PAUSED"
                          ? "paused"
                          : "manual only"}
                    </Badge>
                    <button
                      type="button"
                      onClick={() => setPlannedJob(job)}
                      className="inline-flex items-center gap-1 rounded-lg border border-grid px-2.5 py-1 text-xs font-medium text-ink hover:bg-hairline disabled:opacity-50"
                    >
                      Plan run
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
      </Card>
      {plannedJob && (
        <ActionPlanDialog
          action="run-job"
          title={`Run ${plannedJob.name}`}
          parameters={{ job_id: plannedJob.job_id, job_name: plannedJob.name }}
          allowLegacy={false}
          onClose={() => setPlannedJob(null)}
        />
      )}
    </div>
  );
}

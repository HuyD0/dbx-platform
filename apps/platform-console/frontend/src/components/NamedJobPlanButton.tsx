import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import type { Envelope, JobInfo } from "../lib/types";
import { PlanActionButton } from "./ActionPlanDialog";

export function NamedJobPlanButton({
  expectedName,
  label,
  tone = "default",
}: {
  expectedName: string;
  label: string;
  tone?: "default" | "danger" | "primary";
}) {
  const query = useQuery({
    queryKey: ["jobs"],
    queryFn: () => apiGet<Envelope<JobInfo[]>>("/api/jobs"),
    staleTime: 60_000,
    retry: false,
  });
  const job = query.data?.data.find((candidate) => candidate.name === expectedName);
  if (!job) {
    return (
      <button
        type="button"
        disabled
        title={
          query.isError
            ? "Owned job inventory is unavailable."
            : `Deploy and inventory ${expectedName} before planning a manual run.`
        }
        className="rounded-lg border border-grid px-3 py-1.5 text-xs font-medium text-muted opacity-60"
      >
        {query.isPending ? "Loading job…" : `${label} unavailable`}
      </button>
    );
  }
  return (
    <PlanActionButton
      action="run-job"
      label={label}
      parameters={{ job_id: job.job_id, job_name: job.name }}
      allowLegacy={false}
      tone={tone}
    />
  );
}

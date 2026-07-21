import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { apiGet, apiPost } from "../../lib/api";
import type { Envelope, SavedEstimateSummary } from "../../lib/types";
import { DataTable } from "../DataTable";
import { Card, EmptyState, SectionTitle } from "../ui";
import { LinkDeploymentForm } from "./DeploymentsPanel";

const ESTIMATE_TIERS = ["prototype", "production", "fiduciary"];
const ESTIMATE_SCENARIOS = ["databricks", "azure"];

/** Save-to-library control. The server recomputes the estimate from the saved
 * requirements before storing, so the library can never contain a number the
 * engine would not reproduce. */
export function SaveEstimateButton({
  requirements,
  rigorPct,
}: {
  requirements: Record<string, unknown>;
  rigorPct: number;
}) {
  const [title, setTitle] = useState("");
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: () =>
      apiPost<{ estimate_id: string }>("/api/estimator/estimates/record", {
        title,
        requirements,
        rigor_pct: rigorPct,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["estimator", "estimates"] });
      queryClient.invalidateQueries({ queryKey: ["estimator", "similar"] });
    },
  });
  return (
    <div className="flex flex-wrap items-center gap-2">
      <label className="sr-only" htmlFor="estimate-title">
        Estimate title
      </label>
      <input
        id="estimate-title"
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        placeholder="Name this estimate (e.g. Support doc-chat, FY27)"
        className="w-64 rounded-lg border border-hairline bg-page px-3 py-1.5 text-xs text-ink"
      />
      <button
        type="button"
        disabled={!title.trim() || save.isPending}
        onClick={() => save.mutate()}
        className="rounded-lg bg-series-1 px-3 py-1.5 text-xs font-semibold text-page disabled:opacity-50"
      >
        {save.isPending ? "Saving…" : save.isSuccess ? "Saved" : "Save to library"}
      </button>
      {save.isError && (
        <span role="alert" className="text-xs text-danger">
          {(save.error as Error).message}
        </span>
      )}
    </div>
  );
}

export function EstimateLibrary({
  onReuse,
}: {
  onReuse: (estimate: SavedEstimateSummary) => void;
}) {
  const query = useQuery({
    queryKey: ["estimator", "estimates"],
    queryFn: () => apiGet<Envelope<SavedEstimateSummary[]>>("/api/estimator/estimates"),
    staleTime: 60_000,
  });
  const rows = query.data?.data ?? [];
  return (
    <Card>
      <SectionTitle
        title="Estimate library"
        subtitle="Every saved estimate, with the exact inputs and pricing snapshot it used."
      />
      {rows.length === 0 ? (
        <EmptyState message="No saved estimates yet — compute one and save it to start the library." />
      ) : (
        <DataTable
          rows={rows.map((row) => ({
            title: row.title,
            pattern: row.pattern,
            "requests / month": row.monthly_requests,
            "reviews %": row.rigor_pct,
            "priced on": row.snapshot_date ?? "—",
            saved: row.created_at,
            _row: row,
          }))}
          caption="Saved estimates"
          exportName="ai-cost-planner-library"
          pageSize={8}
          rowAction={(row) => {
            const saved = (row as { _row: SavedEstimateSummary })._row;
            return (
              <div className="flex flex-wrap items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => onReuse(saved)}
                  className="rounded-lg border border-hairline px-2 py-1 text-xs text-ink-2 hover:text-ink"
                >
                  Use as starting point
                </button>
                <LinkDeploymentForm
                  estimateId={saved.estimate_id}
                  tiers={ESTIMATE_TIERS}
                  scenarios={ESTIMATE_SCENARIOS}
                />
              </div>
            );
          }}
          rowActionLabel="Actions"
        />
      )}
    </Card>
  );
}

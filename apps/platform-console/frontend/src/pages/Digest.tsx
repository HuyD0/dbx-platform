import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { apiGet, apiPost } from "../lib/api";
import { timeAgo } from "../lib/format";
import type { Envelope, Row } from "../lib/types";
import { Card, EmptyState, ErrorState, SectionTitle, Skeleton } from "../components/ui";

interface DigestTask {
  state: "running" | "done" | "failed";
  digest?: string | null;
  skipped?: Record<string, string>;
  stored?: boolean;
  error?: string;
}

function useDigestTask() {
  const [taskId, setTaskId] = useState<string | null>(null);
  const [status, setStatus] = useState<DigestTask | null>(null);
  const timer = useRef<number | null>(null);
  const queryClient = useQueryClient();

  const start = useMutation({
    mutationFn: () => apiPost<{ task_id: string }>("/api/digest/generate"),
    onSuccess: (d) => {
      setTaskId(d.task_id);
      setStatus({ state: "running" });
    },
  });

  useEffect(() => {
    if (!taskId || status?.state !== "running") return;
    timer.current = window.setInterval(async () => {
      try {
        const s = await apiGet<DigestTask>(`/api/digest/generate/${taskId}`);
        setStatus(s);
        if (s.state !== "running") {
          if (timer.current) window.clearInterval(timer.current);
          void queryClient.invalidateQueries({ queryKey: ["digests"] });
        }
      } catch {
        // transient poll failure — keep polling
      }
    }, 3000);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [taskId, status?.state, queryClient]);

  return { start, status };
}

export function Digest() {
  const query = useQuery({
    queryKey: ["digests"],
    queryFn: () => apiGet<Envelope<Row[]>>("/api/digest"),
    staleTime: 60_000,
    retry: false,
  });
  const { start, status } = useDigestTask();
  const [openIdx, setOpenIdx] = useState(0);

  const generating = start.isPending || status?.state === "running";

  return (
    <div className="space-y-4">
      <Card>
        <SectionTitle
          title="Generate a fresh digest"
          subtitle="Collects every check and asks the workspace's foundation model for an executive summary — this can take a few minutes"
          right={
            <button
              type="button"
              disabled={generating}
              onClick={() => start.mutate()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
            >
              <Sparkles className="h-3.5 w-3.5" />
              {generating ? "Generating…" : "Generate"}
            </button>
          }
        />
        {start.isError && <ErrorState error={start.error} />}
        {status?.state === "running" && <Skeleton rows={3} />}
        {status?.state === "failed" && (
          <ErrorState error={new Error(status.error ?? "digest generation failed")} />
        )}
        {status?.state === "done" && (
          <div className="space-y-2">
            {status.digest ? (
              <>
                <div className="prose-console text-ink-2">
                  <ReactMarkdown>{status.digest}</ReactMarkdown>
                </div>
                <p className="text-xs text-muted">
                  {status.stored ? "Stored to the digest table." : "Not stored (insert failed)."}
                </p>
              </>
            ) : (
              <p className="text-sm text-ink-2">
                The model was unavailable ({status.error}); findings were collected but no
                summary was produced.
              </p>
            )}
            {status.skipped && Object.keys(status.skipped).length > 0 && (
              <p className="text-xs text-muted">
                Skipped checks: {Object.keys(status.skipped).join(", ")}
              </p>
            )}
          </div>
        )}
      </Card>

      <Card>
        <SectionTitle title="Stored digests" subtitle="Written by the weekly digest job" />
        {query.isPending ? (
          <Skeleton rows={4} />
        ) : query.isError ? (
          <ErrorState error={query.error} />
        ) : query.data.data.length === 0 ? (
          <EmptyState message="No digests stored yet — generate one above or wait for the weekly job." />
        ) : (
          <div className="space-y-2">
            {query.data.data.map((row, i) => (
              <div key={i} className="rounded-lg border border-grid">
                <button
                  type="button"
                  onClick={() => setOpenIdx(openIdx === i ? -1 : i)}
                  className="flex w-full items-center justify-between px-3 py-2 text-left text-xs text-ink hover:bg-hairline"
                  aria-expanded={openIdx === i}
                >
                  <span>
                    {String(row.run_ts)} — last {String(row.days)}d
                  </span>
                  <span className="text-muted">{timeAgo(String(row.run_ts))}</span>
                </button>
                {openIdx === i && (
                  <div className="prose-console border-t border-grid px-3 py-2 text-ink-2">
                    <ReactMarkdown>{String(row.digest ?? "_empty digest_")}</ReactMarkdown>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

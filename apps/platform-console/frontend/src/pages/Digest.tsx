import { useQuery } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import { NamedJobPlanButton } from "../components/NamedJobPlanButton";
import { apiGet } from "../lib/api";
import { timeAgo } from "../lib/format";
import type { Envelope, Row } from "../lib/types";
import { Card, EmptyState, ErrorState, SectionTitle, Skeleton } from "../components/ui";

export function Digest() {
  const query = useQuery({
    queryKey: ["digests"],
    queryFn: () => apiGet<Envelope<Row[]>>("/api/digest"),
    staleTime: 60_000,
    retry: false,
  });
  const [openIdx, setOpenIdx] = useState(0);

  return (
    <div className="space-y-4">
      <Card>
        <SectionTitle
          title="Executive digest"
          subtitle="Collects current findings and asks the workspace model for a cited summary. Generation incurs model usage and writes a record, so it is governed."
          right={
            <NamedJobPlanButton
              expectedName="[dbx-platform] platform-digest"
              label="Plan fresh digest"
              tone="primary"
            />
          }
        />
        <div className="flex items-start gap-2 rounded-xl border border-grid bg-hairline/20 p-3 text-xs leading-5 text-ink-2">
          <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
          AI prepares the narrative; the approval plan shows its model, data window, estimated
          cost, and destination before the job starts.
        </div>
      </Card>

      <Card>
        <SectionTitle title="Stored digests" subtitle="Written by approved or scheduled digest jobs" />
        {query.isPending ? (
          <Skeleton rows={4} />
        ) : query.isError ? (
          <ErrorState error={query.error} />
        ) : query.data.data.length === 0 ? (
          <EmptyState
            message="No digests are stored yet. Plan one above or wait for the scheduled job."
            positive={false}
          />
        ) : (
          <div className="space-y-2">
            {query.data.data.map((row, index) => (
              <div key={index} className="rounded-lg border border-grid">
                <button
                  type="button"
                  onClick={() => setOpenIdx(openIdx === index ? -1 : index)}
                  className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-xs text-ink hover:bg-hairline"
                  aria-expanded={openIdx === index}
                >
                  <span>
                    {String(row.run_ts)} — last {String(row.days)}d
                  </span>
                  <span className="shrink-0 text-muted">{timeAgo(String(row.run_ts))}</span>
                </button>
                {openIdx === index && (
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

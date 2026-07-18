import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { useState } from "react";
import { apiGet } from "../lib/api";
import type { DashboardInfo, Envelope } from "../lib/types";
import { AsOf, Card, EmptyState, ErrorState, SectionTitle, Skeleton } from "../components/ui";

/** Strip the shared "[dbx-platform]" prefix so the tabs read as titles. */
const shortName = (name: string) => name.replace("[dbx-platform]", "").trim() || name;

export function Dashboards() {
  const query = useQuery({
    queryKey: ["dashboards"],
    queryFn: () => apiGet<Envelope<DashboardInfo[]>>("/api/dashboards"),
    staleTime: 300_000,
    retry: false,
  });
  const [selectedName, setSelectedName] = useState<string | null>(null);

  const dashboards = query.data?.data ?? [];
  const selected = dashboards.find((d) => d.name === selectedName) ?? dashboards[0];

  return (
    <div className="space-y-4">
      <Card>
        <SectionTitle
          title="Dashboards"
          subtitle="The bundle's AI/BI dashboards, embedded. Blank frame? A workspace admin must approve the app's domain for embedding (docs/runbook.md)."
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
          <Skeleton rows={4} />
        ) : query.isError ? (
          <ErrorState error={query.error} />
        ) : dashboards.length === 0 ? (
          <EmptyState message="No [dbx-platform] dashboards visible — deploy the bundle first." />
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              {dashboards.map((d) => (
                <button
                  key={d.name}
                  type="button"
                  onClick={() => setSelectedName(d.name)}
                  className={`rounded-lg border px-2.5 py-1 text-xs font-medium ${
                    selected?.name === d.name
                      ? "border-accent/40 bg-accent/15 text-accent"
                      : "border-grid text-ink-2 hover:bg-hairline"
                  }`}
                >
                  {shortName(d.name)}
                </button>
              ))}
            </div>
            {selected && (
              <>
                <iframe
                  key={selected.name}
                  src={selected.embed_url}
                  title={selected.name}
                  loading="lazy"
                  className="h-[75vh] w-full rounded-xl border border-grid bg-white"
                />
                <a
                  href={selected.url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-xs text-ink-2 hover:text-ink hover:underline"
                >
                  <ExternalLink className="h-3 w-3" />
                  Open in workspace
                </a>
              </>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}

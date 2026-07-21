import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { apiGet } from "../lib/api";
import type { Envelope, Row } from "../lib/types";
import { DataTable } from "./DataTable";
import {
  AsOf,
  CapabilityNotice,
  Card,
  EmptyState,
  ErrorState,
  SectionTitle,
  Skeleton,
} from "./ui";

/** One check = one card: fetch → skeleton → table/empty/error, with an
 * as-of stamp and explicit refresh. The workhorse of every findings page. */
export function FindingsSection({
  title,
  subtitle,
  path,
  params,
  emptyMessage = "No findings — all clean.",
  actionSlot,
  render,
  renderWhenEmpty = false,
}: {
  title: string;
  subtitle?: string;
  path: string;
  params?: Record<string, string | number>;
  emptyMessage?: string;
  actionSlot?: ReactNode;
  render?: (rows: Row[]) => ReactNode;
  renderWhenEmpty?: boolean;
}) {
  const queryClient = useQueryClient();
  const queryKey = [path, params];
  const query = useQuery({
    queryKey,
    queryFn: () => apiGet<Envelope<Row[]>>(path, params),
    staleTime: 60_000,
    retry: false,
  });

  const refresh = async () => {
    await queryClient.fetchQuery({
      queryKey,
      queryFn: () => apiGet<Envelope<Row[]>>(path, { ...params, refresh: true }),
    });
  };

  const rows = query.data?.data ?? [];
  return (
    <Card>
      <SectionTitle
        title={title}
        subtitle={subtitle}
        right={
          <div className="flex items-center gap-2">
            {actionSlot}
            <AsOf
              asOf={query.data?.as_of}
              cached={query.data?.cached}
              onRefresh={refresh}
              refreshing={query.isFetching}
            />
          </div>
        }
      />
      {query.isPending ? (
        <Skeleton rows={3} />
      ) : query.isError ? (
        <ErrorState error={query.error} />
      ) : rows.length === 0 &&
        query.data.source_status &&
        query.data.source_status.status !== "healthy" ? (
        <CapabilityNotice
          title={`${query.data.source_status.source ?? "Evidence source"} coverage is ${query.data.source_status.status}`}
          description={
            query.data.source_status.notes ??
            "No finding can be asserted until this source is available."
          }
        />
      ) : rows.length === 0 && render && renderWhenEmpty ? (
        render(rows)
      ) : rows.length === 0 ? (
        <EmptyState message={emptyMessage} />
      ) : render ? (
        render(rows)
      ) : (
        <DataTable rows={rows} />
      )}
    </Card>
  );
}

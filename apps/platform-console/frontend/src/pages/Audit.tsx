import { useQuery } from "@tanstack/react-query";
import { Fingerprint, ScrollText } from "lucide-react";
import { DataTable } from "../components/DataTable";
import {
  AsOf,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  SectionTitle,
  Skeleton,
} from "../components/ui";
import { apiGet } from "../lib/api";
import type { ActionRequest, Envelope, Row } from "../lib/types";

function actions(data: ActionRequest[] | { items?: ActionRequest[] }): ActionRequest[] {
  const items = Array.isArray(data) ? data : (data.items ?? []);
  return items.map((row) => ({
    ...row,
    target_count: row.target_count ?? row.targets.length,
  }));
}

/** Adapt strict action records only at the generic legacy table boundary. */
function actionAuditRows(items: ActionRequest[]): Row[] {
  return items.map((action) => ({
    action_type: action.action_type,
    status: action.status,
    risk: action.risk,
    target_count: action.target_count ?? action.targets.length,
    proposer_email: action.proposer_email,
    created_at: action.created_at,
    updated_at: action.updated_at,
    expires_at: action.expires_at,
    terminal_reason: action.terminal_reason,
    plan_hash: action.plan_hash,
  }));
}

export function Audit() {
  const query = useQuery({
    queryKey: ["action-audit"],
    queryFn: () =>
      apiGet<Envelope<ActionRequest[] | { items?: ActionRequest[] }>>("/api/action-requests", {
        include_events: true,
      }),
    staleTime: 15_000,
    retry: false,
  });
  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Accountability"
        title="Audit"
        description="Follow every proposal from immutable plan through approval, execution, verification and measured outcome."
        actions={
          query.data ? (
            <AsOf
              asOf={query.data.as_of}
              cached={query.data.cached}
              onRefresh={() => query.refetch()}
              refreshing={query.isFetching}
            />
          ) : undefined
        }
      />

      <div className="grid gap-3 sm:grid-cols-2">
        <Card>
          <div className="flex items-start gap-3">
            <Fingerprint className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
            <div>
              <h2 className="text-sm font-semibold text-ink">Plan integrity</h2>
              <p className="mt-1 text-xs leading-5 text-muted">
                SHA-256 binds the exact targets, preconditions, impact, rollback and verification.
              </p>
            </div>
          </div>
        </Card>
        <Card>
          <div className="flex items-start gap-3">
            <ScrollText className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
            <div>
              <h2 className="text-sm font-semibold text-ink">Append-only evidence</h2>
              <p className="mt-1 text-xs leading-5 text-muted">
                Rejections, expiry, drift, failure and rollback remain visible; they are not erased.
              </p>
            </div>
          </div>
        </Card>
      </div>

      <Card>
        <SectionTitle title="Action and event history" subtitle="Exportable for independent review" />
        {query.isPending ? (
          <Skeleton rows={7} />
        ) : query.isError ? (
          <ErrorState error={query.error} />
        ) : actions(query.data.data).length > 0 ? (
          <DataTable
            rows={actionAuditRows(actions(query.data.data))}
            exportName="action-audit"
            caption="Action request and event audit history"
            columns={[
              "action_type",
              "status",
              "risk",
              "target_count",
              "proposer_email",
              "created_at",
              "updated_at",
              "expires_at",
              "terminal_reason",
              "plan_hash",
            ]}
          />
        ) : (
          <EmptyState message="No governed action has been recorded yet." />
        )}
      </Card>
    </div>
  );
}

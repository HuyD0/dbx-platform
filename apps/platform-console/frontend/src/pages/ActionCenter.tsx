import { useQuery } from "@tanstack/react-query";
import {
  CheckCircle2,
  CircleDotDashed,
  Fingerprint,
  ShieldCheck,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { PlanActionButton } from "../components/ActionPlanDialog";
import { ActionReviewDialog } from "../components/ActionReviewDialog";
import { DataTable } from "../components/DataTable";
import {
  AsOf,
  Badge,
  CapabilityNotice,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  SectionTitle,
  Skeleton,
  Tabs,
  statusTone,
} from "../components/ui";
import { apiGet, isUnavailable } from "../lib/api";
import type { ActionRequest, ActionStatus, Envelope } from "../lib/types";

const TABS = [
  { id: "needs_review", label: "Needs your review" },
  { id: "recommendations", label: "Recommendations" },
  { id: "in_progress", label: "In progress" },
  { id: "history", label: "History" },
];

const ACTION_LABELS: Record<string, string> = {
  "stale-clusters": "Clean up stale clusters",
  "orphaned-jobs": "Pause orphaned job schedules",
  "token-revoke": "Revoke old access tokens",
  "policy-sync": "Synchronize cluster policies",
  "run-job": "Run a governed platform job",
  "configure-budget": "Change a cost budget",
  "runtime.hibernate": "Hibernate platform resources",
  "runtime.wake": "Wake platform resources",
};

function actionLabel(value: unknown): string {
  const key = String(value ?? "");
  return ACTION_LABELS[key] ?? key.replaceAll("-", " ").replaceAll("_", " ");
}

const LEGACY_ACTIONS = [
  {
    action: "stale-clusters",
    title: "Clean up stale clusters",
    description: "Review stale and long-running compute. Permanent deletion is excluded.",
    risk: "medium",
  },
  {
    action: "orphaned-jobs",
    title: "Pause orphaned jobs",
    description: "Pause schedules whose creator is inactive; never delete a job.",
    risk: "medium",
  },
  {
    action: "token-revoke",
    title: "Revoke over-age PATs",
    description: "Irreversible credential revocation with explicit target confirmation.",
    risk: "high",
  },
  {
    action: "policy-sync",
    title: "Synchronize cluster policies",
    description: "Create or update managed policies; leave unmanaged policies untouched.",
    risk: "medium",
  },
];

function rowsFromEnvelope(
  envelope: Envelope<ActionRequest[] | { items?: ActionRequest[] }>,
): ActionRequest[] {
  const items = Array.isArray(envelope.data) ? envelope.data : (envelope.data.items ?? []);
  return items.map((row) => {
    const normalized: ActionRequest = {
      ...row,
      target_count: row.target_count ?? row.targets.length,
    };
    return {
      ...normalized,
      effective_status: effectiveStatus(normalized),
    };
  });
}

function effectiveStatus(row: ActionRequest): ActionStatus {
  const evaluatedAt = Date.parse(row.evaluated_at);
  const expiresAt = Date.parse(row.expires_at);
  const current = row.effective_status || row.status;
  if (
    ["AWAITING_APPROVAL", "APPROVED"].includes(current) &&
    Number.isFinite(evaluatedAt) &&
    Number.isFinite(expiresAt) &&
    expiresAt <= evaluatedAt
  ) {
    return "EXPIRED";
  }
  return current;
}

function canApprove(row: ActionRequest): boolean {
  return row.can_approve === true && effectiveStatus(row) === "AWAITING_APPROVAL";
}

function matchesTab(row: ActionRequest, tab: string): boolean {
  const status = effectiveStatus(row);
  if (tab === "recommendations") return false;
  if (tab === "needs_review") return status === "AWAITING_APPROVAL";
  if (tab === "in_progress") {
    return ["APPROVED", "EXECUTING", "VERIFYING"].includes(status);
  }
  return !["AWAITING_APPROVAL", "APPROVED", "EXECUTING", "VERIFYING"].includes(status);
}

function actionTableRows(actions: ActionRequest[]) {
  return actions.map((action) => ({
    request: actionLabel(action.action_type),
    state: effectiveStatus(action).replaceAll("_", " ").toLowerCase(),
    can_approve: canApprove(action),
    risk: action.risk,
    affected_resources: action.target_count ?? action.targets.length,
    requested_by: action.proposer_email,
    created_at: action.created_at,
    expires_at: action.expires_at,
    _action_id: action.action_id,
  }));
}

export function ActionCenter() {
  const [tab, setTab] = useState("needs_review");
  const [reviewId, setReviewId] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["action-requests"],
    queryFn: () =>
      apiGet<Envelope<ActionRequest[] | { items?: ActionRequest[] }>>("/api/action-requests"),
    staleTime: 15_000,
    retry: false,
  });
  const rows = useMemo(() => (query.data ? rowsFromEnvelope(query.data) : []), [query.data]);
  const filtered = rows.filter((row) => matchesTab(row, tab));
  const unavailable = query.isError && isUnavailable(query.error);
  const tabs = TABS.map((item) => ({
    ...item,
    badge: rows.filter((row) => matchesTab(row, item.id)).length,
  }));
  const closeReview = useCallback(() => setReviewId(null), []);
  const refreshActions = query.refetch;
  const handleChanged = useCallback(() => {
    void refreshActions();
  }, [refreshActions]);

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Governed changes"
        title="Review & Approve"
        description="Review recommended changes, approve one exact plan, and confirm the outcome. Nothing runs automatically."
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

      <div className="grid gap-3 sm:grid-cols-3">
        <Card>
          <div className="flex items-center gap-2">
            <CircleDotDashed className="h-4 w-4 text-status-warning" />
            <span className="text-xs font-medium text-muted">1 · Review</span>
          </div>
          <p className="mt-2 text-sm font-medium text-ink">
            {rows.filter((row) => matchesTab(row, "needs_review")).length} waiting for you
          </p>
          <p className="mt-1 text-[11px] text-muted">
            Understand the evidence, exact targets, impact and risk.
          </p>
        </Card>
        <Card>
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-status-warning" />
            <span className="text-xs font-medium text-muted">2 · Approve</span>
          </div>
          <p className="mt-2 text-sm font-medium text-ink">Authorize one exact plan</p>
          <p className="mt-1 text-[11px] text-muted">
            Plans expire after 15 minutes, are single-use and are rechecked.
          </p>
        </Card>
        <Card>
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-status-good" />
            <span className="text-xs font-medium text-muted">3 · Verify</span>
          </div>
          <p className="mt-2 text-sm font-medium text-ink">Confirm what changed</p>
          <p className="mt-1 text-[11px] text-muted">
            Execution and verification are recorded in an append-only history.
          </p>
        </Card>
      </div>

      <Tabs tabs={tabs} active={tab} onChange={setTab} label="Review and approve views" />

      <Card>
        <SectionTitle
          title={TABS.find((item) => item.id === tab)?.label ?? "Actions"}
          subtitle={
            tab === "needs_review"
              ? "Open a request to review its exact targets and decide whether it should run."
              : "Every request keeps its complete plan, decision and outcome history."
          }
          right={
            <details className="text-[11px] text-muted">
              <summary className="cursor-pointer">Technical safeguards</summary>
              <span className="mt-1 inline-flex items-center gap-1">
                <Fingerprint className="h-3.5 w-3.5" />
                SHA-256 plan binding
              </span>
            </details>
          }
        />
        {query.isPending ? (
          <Skeleton rows={5} />
        ) : query.isError && !unavailable ? (
          <ErrorState error={query.error} />
        ) : unavailable ? (
          <CapabilityNotice
            title="Durable approval ledger is not connected yet"
            description="Existing dry-run remediations remain available below. New job and budget actions fail closed until the action-request API is enabled."
          />
        ) : filtered.length === 0 ? (
          <EmptyState
            message={
              tab === "needs_review"
                ? "No plan is waiting for approval."
                : `No ${TABS.find((item) => item.id === tab)?.label.toLowerCase()} to show.`
            }
          />
        ) : (
          <DataTable
            rows={actionTableRows(filtered)}
            exportName={`action-center-${tab}`}
            caption={`${tab} action requests`}
            columns={[
              "request",
              "state",
              "risk",
              "affected_resources",
              "requested_by",
              "created_at",
              "expires_at",
            ]}
            rowAction={(row) => {
              const id = typeof row._action_id === "string" ? row._action_id : "";
              const readyForApproval = row.can_approve === true;
              return (
                <button
                  type="button"
                  disabled={!id}
                  onClick={() => setReviewId(id)}
                  aria-label={`${readyForApproval ? "Review approval" : "Review action"} ${String(
                    row.request ?? id,
                  )}`}
                  className="min-h-8 rounded-lg border border-grid px-2.5 py-1 text-xs font-medium text-ink hover:bg-hairline disabled:opacity-40"
                >
                  {readyForApproval ? "Review approval" : "Review"}
                </button>
              );
            }}
          />
        )}
      </Card>

      {(tab === "recommendations" || unavailable) && (
        <section aria-labelledby="available-plans-title">
          <div className="mb-3">
            <h2 id="available-plans-title" className="text-sm font-semibold text-ink">
              Create a plan
            </h2>
            <p className="mt-0.5 text-xs text-muted">
              Creating a plan only prepares exact targets for review. It never executes a change.
            </p>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {LEGACY_ACTIONS.map((item) => (
              <Card key={item.action}>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-semibold text-ink">{item.title}</h3>
                    <p className="mt-1 text-xs leading-5 text-muted">{item.description}</p>
                  </div>
                  <Badge tone={statusTone(item.risk)}>{item.risk} risk</Badge>
                </div>
                <div className="mt-3">
                  <PlanActionButton action={item.action} label={`Review ${item.title.toLowerCase()}`} />
                </div>
              </Card>
            ))}
          </div>
        </section>
      )}
      {reviewId && (
        <ActionReviewDialog
          actionId={reviewId}
          onClose={closeReview}
          onChanged={handleChanged}
        />
      )}
    </div>
  );
}

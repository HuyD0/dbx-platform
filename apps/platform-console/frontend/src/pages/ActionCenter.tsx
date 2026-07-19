import { useQuery } from "@tanstack/react-query";
import {
  BadgeCheck,
  CircleDotDashed,
  FileSearch,
  Fingerprint,
  History,
  Play,
  ShieldCheck,
  UserCheck,
} from "lucide-react";
import { useMemo, useState } from "react";
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
import type { ActionRequest, Envelope } from "../lib/types";

const TABS = [
  { id: "recommendations", label: "Recommendations" },
  { id: "approval", label: "Awaiting approval" },
  { id: "activity", label: "Activity" },
  { id: "failed", label: "Failed / rolled back" },
];

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

const ACTION_LIFECYCLE = [
  {
    label: "Evidence",
    description: "Canonical finding and current state",
    icon: FileSearch,
  },
  {
    label: "Immutable plan",
    description: "Exact targets, hash, TTL and rollback",
    icon: Fingerprint,
  },
  {
    label: "Human approval",
    description: "Current membership and explicit confirmation",
    icon: UserCheck,
  },
  {
    label: "Execution",
    description: "Dedicated least-privileged executor",
    icon: Play,
    tone: "process",
  },
  {
    label: "Verification",
    description: "Revalidated outcome and append-only events",
    icon: BadgeCheck,
    tone: "complete",
  },
] as const;

function rowsFromEnvelope(
  envelope: Envelope<ActionRequest[] | { items?: ActionRequest[] }>,
): ActionRequest[] {
  const items = Array.isArray(envelope.data) ? envelope.data : (envelope.data.items ?? []);
  return items.map((row) => ({
    ...row,
    target_count:
      row.target_count ??
      (Array.isArray(row.targets) ? row.targets.length : undefined),
  }));
}

function matchesTab(row: ActionRequest, tab: string): boolean {
  const status = String(row.status ?? "").toUpperCase();
  if (tab === "recommendations") return ["RECOMMENDED", "DRAFT", "PROPOSED"].includes(status);
  if (tab === "approval") return ["AWAITING_APPROVAL", "APPROVED"].includes(status);
  if (tab === "failed") {
    return ["FAILED", "ROLLED_BACK", "REJECTED", "EXPIRED", "STALE"].includes(status);
  }
  return !["RECOMMENDED", "DRAFT", "PROPOSED", "AWAITING_APPROVAL"].includes(status);
}

export function ActionCenter() {
  const [tab, setTab] = useState("recommendations");
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

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Governed operations"
        title="Action Center"
        description="One place to review evidence, approve an immutable plan, and verify what actually changed."
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

      <Card>
        <SectionTitle
          title="Governed action lifecycle"
          subtitle="Every mutation follows the same fail-closed sequence."
          right={<Badge tone="info">Required control path</Badge>}
        />
        <ol className="blueprint-process" aria-label="Governed action lifecycle">
          {ACTION_LIFECYCLE.map((step, index) => {
            const Icon = step.icon;
            const tone = "tone" in step ? step.tone : undefined;
            return (
              <li
                key={step.label}
                className="blueprint-process-step"
                data-tone={tone}
              >
                <span className="blueprint-process-node" aria-hidden="true">
                  <span>{index + 1}</span>
                </span>
                <div className="flex items-center gap-2">
                  <Icon className="h-4 w-4 shrink-0 text-accent" aria-hidden="true" />
                  <span className="text-xs font-semibold text-ink">{step.label}</span>
                </div>
                <p className="mt-1 text-[11px] leading-4 text-muted">{step.description}</p>
              </li>
            );
          })}
        </ol>
      </Card>

      <div className="grid gap-3 sm:grid-cols-3">
        <Card>
          <div className="flex items-center gap-2">
            <CircleDotDashed className="h-4 w-4 text-status-warning" />
            <span className="text-xs text-muted">Awaiting approval</span>
          </div>
          <div className="mt-2 text-2xl font-semibold text-ink">
            {rows.filter((row) => matchesTab(row, "approval")).length || "—"}
          </div>
        </Card>
        <Card>
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-status-good" />
            <span className="text-xs text-muted">Control</span>
          </div>
          <p className="mt-2 text-sm font-medium text-ink">One human, exact plan</p>
          <p className="mt-1 text-[11px] text-muted">15-minute TTL · single use · revalidated</p>
        </Card>
        <Card>
          <div className="flex items-center gap-2">
            <History className="h-4 w-4 text-accent" />
            <span className="text-xs text-muted">Audit trail</span>
          </div>
          <p className="mt-2 text-sm font-medium text-ink">Append-only outcomes</p>
          <p className="mt-1 text-[11px] text-muted">Plan → approval → execution → verification</p>
        </Card>
      </div>

      <Tabs tabs={tabs} active={tab} onChange={setTab} label="Action Center views" />

      <Card>
        <SectionTitle
          title={TABS.find((item) => item.id === tab)?.label ?? "Actions"}
          subtitle="AI prose cannot alter the server-generated targets, impact, rollback or verification."
          right={
            <span className="inline-flex items-center gap-1 text-[11px] text-muted">
              <Fingerprint className="h-3.5 w-3.5" />
              SHA-256 plan binding
            </span>
          }
        />
        {query.isPending ? (
          <Skeleton rows={5} />
        ) : query.isError && !unavailable ? (
          <ErrorState error={query.error} />
        ) : unavailable ? (
          <CapabilityNotice
            title="Durable approval ledger is not connected yet"
            description="Existing dry-run remediations remain available below. New runtime, job and budget actions fail closed until the action-request API is enabled."
          />
        ) : filtered.length === 0 ? (
          <EmptyState
            message={
              tab === "approval"
                ? "No plan is waiting for approval."
                : `No ${TABS.find((item) => item.id === tab)?.label.toLowerCase()} to show.`
            }
          />
        ) : (
          <DataTable
            rows={filtered}
            exportName={`action-center-${tab}`}
            caption={`${tab} action requests`}
            columns={[
              "action_type",
              "status",
              "risk",
              "target_count",
              "proposer_email",
              "created_at",
              "expires_at",
              "plan_hash",
            ]}
            rowAction={(row) => {
              const id = String(row.action_id ?? row.plan_id ?? row.id ?? "");
              return (
                <button
                  type="button"
                  disabled={!id}
                  onClick={() => setReviewId(id)}
                  className="rounded-lg border border-grid px-2.5 py-1 text-xs font-medium text-ink hover:bg-hairline disabled:opacity-40"
                >
                  Review
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
              Available deterministic planners
            </h2>
            <p className="mt-0.5 text-xs text-muted">
              Planning is read-only. Approval remains disabled when the executor is not configured.
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
          onClose={() => setReviewId(null)}
          onChanged={() => query.refetch()}
        />
      )}
    </div>
  );
}

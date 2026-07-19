import { useMutation, useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Fingerprint,
  Info,
  ShieldCheck,
  X,
  XCircle,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { apiGet, apiPost } from "../lib/api";
import { dateTime } from "../lib/format";
import type {
  ActionEvent,
  ActionRequestDetail,
  ActionStatus,
  Row,
} from "../lib/types";
import { DataTable } from "./DataTable";
import { Badge, ErrorState, Skeleton, statusTone } from "./ui";

function useServerClock(evaluatedAt?: string, expiresAt?: string): number | null {
  const parsedAnchor = Date.parse(evaluatedAt ?? "");
  const [clock, setClock] = useState<{
    evaluatedAt: string | undefined;
    value: number | null;
  }>({ evaluatedAt, value: Number.isFinite(parsedAnchor) ? parsedAnchor : null });
  useEffect(() => {
    const serverAnchor = Date.parse(evaluatedAt ?? "");
    if (!Number.isFinite(serverAnchor)) {
      setClock({ evaluatedAt, value: null });
      return;
    }
    const localAnchor = Date.now();
    const update = () =>
      setClock({ evaluatedAt, value: serverAnchor + (Date.now() - localAnchor) });
    update();
    const interval = window.setInterval(update, 1_000);
    const expiry = Date.parse(expiresAt ?? "");
    const boundaryDelay = Number.isFinite(expiry) ? expiry - serverAnchor : NaN;
    const boundary =
      Number.isFinite(boundaryDelay) && boundaryDelay >= 0
        ? window.setTimeout(update, boundaryDelay + 1)
        : null;
    return () => {
      window.clearInterval(interval);
      if (boundary !== null) window.clearTimeout(boundary);
    };
  }, [evaluatedAt, expiresAt]);
  if (clock.evaluatedAt !== evaluatedAt) {
    return Number.isFinite(parsedAnchor) ? parsedAnchor : null;
  }
  return clock.value;
}

function effectiveStatus(action: ActionRequestDetail, serverNow: number | null): ActionStatus {
  const expiry = Date.parse(action.expires_at);
  if (
    ["AWAITING_APPROVAL", "APPROVED"].includes(action.effective_status) &&
    serverNow !== null &&
    Number.isFinite(expiry) &&
    expiry <= serverNow
  ) {
    return "EXPIRED";
  }
  return action.effective_status;
}

/** The generic table remains a legacy row renderer; adapt strict events explicitly. */
function actionEventRows(events: ActionEvent[]): Row[] {
  return events.map((event) => ({
    event_id: event.event_id,
    event_type: event.event_type,
    from_status: event.from_status,
    to_status: event.to_status,
    actor_id: event.actor_id,
    event_ts: event.event_ts,
    details: event.details,
  }));
}

function describeDetail(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return `${value.length} item${value.length === 1 ? "" : "s"}`;
  if (typeof value === "object" && value !== null) {
    const keys = Object.keys(value);
    if (keys.length === 0) return "No entries";
    return keys
      .slice(0, 3)
      .map((key) => key.replaceAll("_", " "))
      .join(", ");
  }
  return String(value);
}

function Detail({
  label,
  description,
  value,
}: {
  label: string;
  description: string;
  value: unknown;
}) {
  if (value === undefined || value === null || value === "") return null;
  const textValue = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return (
    <details className="group rounded-xl border border-grid bg-surface p-3 shadow-sm" open>
      <summary className="cursor-pointer list-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-primary">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wide text-muted">{label}</p>
            <p className="mt-1 text-sm font-medium text-ink">{describeDetail(value)}</p>
            <p className="mt-1 text-xs leading-5 text-muted">{description}</p>
          </div>
          <span className="rounded-full border border-grid px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-muted">
            Details
          </span>
        </div>
      </summary>
      <pre className="mt-3 max-h-80 overflow-auto rounded-lg border border-grid bg-page p-3 font-mono text-xs leading-5 text-ink-2">
        {textValue}
      </pre>
    </details>
  );
}

export function ActionReviewDialog({
  actionId,
  onClose,
  onChanged,
}: {
  actionId: string;
  onClose: () => void;
  onChanged?: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("");
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const confirmApprovalRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const previousFocus = useRef<HTMLElement | null>(
    document.activeElement instanceof HTMLElement ? document.activeElement : null,
  );
  const query = useQuery({
    queryKey: ["action-request", actionId],
    queryFn: () => apiGet<ActionRequestDetail>(`/api/action-requests/${actionId}`),
    retry: false,
  });
  const approve = useMutation({
    mutationFn: (action: ActionRequestDetail) =>
      apiPost<ActionRequestDetail>(`/api/action-requests/${action.action_id}/approve`, {
        plan_hash: action.plan_hash,
      }),
    onSuccess: () => {
      void query.refetch();
      onChanged?.();
    },
  });
  const reject = useMutation({
    mutationFn: (action: ActionRequestDetail) =>
      apiPost<ActionRequestDetail>(`/api/action-requests/${action.action_id}/reject`, {
        plan_hash: action.plan_hash,
        reason: reason.trim() || "Rejected by approver.",
      }),
    onSuccess: () => {
      void query.refetch();
      onChanged?.();
    },
  });

  useEffect(() => {
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.requestAnimationFrame(() => closeRef.current?.focus());
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = originalOverflow;
      document.removeEventListener("keydown", onKeyDown);
      previousFocus.current?.focus();
    };
  }, [onClose]);

  const action = query.data;
  const items = action?.targets ?? [];
  const serverNow = useServerClock(action?.evaluated_at, action?.expires_at);
  const displayStatus = action ? effectiveStatus(action, serverNow) : "";
  const pending = displayStatus === "AWAITING_APPROVAL";
  const expired = displayStatus === "EXPIRED";
  const expiry = Date.parse(action?.expires_at ?? "");
  const timingValid = serverNow !== null && Number.isFinite(expiry) && expiry > serverNow;
  const canApprove =
    action !== undefined &&
    pending &&
    timingValid &&
    action.can_approve === true;

  useEffect(() => {
    if (confirming) {
      window.requestAnimationFrame(() => confirmApprovalRef.current?.focus());
    }
  }, [confirming]);

  useEffect(() => {
    if (canApprove || !confirming) return;
    setConfirming(false);
    window.requestAnimationFrame(() => closeRef.current?.focus());
  }, [canApprove, confirming]);

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-3"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="glass-strong max-h-[94vh] w-full max-w-4xl overflow-y-auto rounded-2xl p-4 shadow-2xl sm:p-5"
      >
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <p className="mb-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
              Existing immutable request
            </p>
            <h2 id={titleId} className="text-base font-semibold text-ink">
              Review action request
            </h2>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close action review"
            className="min-h-8 min-w-8 rounded-lg p-1.5 text-muted hover:bg-hairline"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {query.isPending && <Skeleton rows={7} />}
        {query.isError && <ErrorState error={query.error} />}
        {action && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2 rounded-xl border border-grid bg-page/30 p-3 text-xs">
              <Badge tone={statusTone(displayStatus)}>{displayStatus.replaceAll("_", " ")}</Badge>
              <Badge tone={statusTone(action.risk)}>{String(action.risk)} risk</Badge>
              <span className="font-medium text-ink">{action.action_type}</span>
              <span className="inline-flex min-w-0 items-center gap-1 font-mono text-[10px] text-muted">
                <Fingerprint className="h-3.5 w-3.5" />
                <span title={action.plan_hash}>{action.plan_hash.slice(0, 16)}…</span>
              </span>
              <span className="ml-auto text-muted">expires {dateTime(action.expires_at)}</span>
              {action.evaluated_at && (
                <span className="text-muted">evaluated {dateTime(action.evaluated_at)}</span>
              )}
              {String(action.status).toUpperCase() !== displayStatus && (
                <span className="basis-full text-muted">
                  Ledger status: {String(action.status).replaceAll("_", " ")}
                </span>
              )}
            </div>

            {items.length > 0 && (
              <DataTable
                rows={items}
                pageSize={6}
                exportName={`action-${action.action_id}`}
                caption={`Exact targets for ${action.action_type}`}
              />
            )}

            <section aria-labelledby="plan-review-heading" className="space-y-3">
              <div>
                <h3 id="plan-review-heading" className="text-sm font-semibold text-ink">
                  Plan review guide
                </h3>
                <p className="mt-1 text-xs leading-5 text-muted">
                  Start with the plain-language summary on each card. Expand or scroll the details
                  only when you need the exact immutable JSON used for approval and verification.
                </p>
              </div>
              <div className="grid gap-3 md:grid-cols-3">
                <Detail
                  label="Impact"
                  description="What will change if this exact plan is approved."
                  value={action.impact}
                />
                <Detail
                  label="Rollback"
                  description="How the executor would restore the prior state if needed."
                  value={action.rollback}
                />
                <Detail
                  label="Verification"
                  description="Checks that must pass before or after execution."
                  value={action.verification}
                />
              </div>
            </section>

            {action.events.length > 0 && (
              <DataTable
                rows={actionEventRows(action.events)}
                pageSize={6}
                exportName={`action-${action.action_id}-events`}
                caption="Action audit events"
              />
            )}

            {pending && action.actions_enabled && canApprove && (
              <div className="grid gap-3 rounded-xl border border-status-warning/30 bg-status-warning/5 p-3 md:grid-cols-2">
                <div>
                  {!confirming ? (
                    <>
                      <p className="text-xs leading-5 text-ink-2">
                        Approval applies only to this exact plan hash and is revalidated before
                        execution.
                      </p>
                      <button
                        type="button"
                        disabled={approve.isPending}
                        onClick={() => setConfirming(true)}
                        className="mt-2 inline-flex min-h-11 items-center gap-2 rounded-lg bg-brand-mid px-3 py-2 text-sm font-medium text-white hover:bg-brand-maroon disabled:opacity-40"
                      >
                        <ShieldCheck className="h-4 w-4" />
                        Approve action
                      </button>
                    </>
                  ) : (
                    <div role="alertdialog" aria-label="Confirm approval">
                      <h3 className="text-sm font-semibold text-ink">Confirm approval</h3>
                      <p className="mt-1 text-xs leading-5 text-ink-2">
                        Approve this exact {String(action.risk).toLowerCase()}-risk plan for{" "}
                        {items.length} target{items.length === 1 ? "" : "s"}?
                      </p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => setConfirming(false)}
                          className="min-h-11 rounded-lg border border-grid px-3 py-2 text-sm font-medium text-ink hover:bg-hairline"
                        >
                          Back
                        </button>
                        <button
                          ref={confirmApprovalRef}
                          type="button"
                          disabled={approve.isPending}
                          onClick={() => approve.mutate(action)}
                          className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-brand-mid px-3 py-2 text-sm font-medium text-white hover:bg-brand-maroon disabled:opacity-40"
                        >
                          <ShieldCheck className="h-4 w-4" />
                          {approve.isPending ? "Approving…" : "Confirm approval"}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
                <div>
                  <label className="text-xs leading-5 text-ink-2">
                    Rejection reason
                    <textarea
                      value={reason}
                      onChange={(event) => setReason(event.target.value)}
                      rows={2}
                      maxLength={1000}
                      className="mt-2 w-full resize-y rounded-lg border border-grid bg-page px-3 py-2 text-sm text-ink"
                    />
                  </label>
                  <button
                    type="button"
                    disabled={reject.isPending}
                    onClick={() => reject.mutate(action)}
                    className="mt-2 inline-flex min-h-11 items-center gap-2 rounded-lg border border-status-critical/40 px-3 py-2 text-sm font-medium text-status-critical hover:bg-critical-surface disabled:opacity-40"
                  >
                    <XCircle className="h-4 w-4" />
                    {reject.isPending ? "Rejecting…" : "Reject plan"}
                  </button>
                </div>
              </div>
            )}
            {pending && !action.actions_enabled && (
              <p className="rounded-lg border border-grid bg-hairline/30 p-3 text-xs leading-5 text-ink-2">
                This deployment is proposal-only. Review and export remain available; approval
                and executor submission are disabled.
              </p>
            )}
            {pending && action.actions_enabled && !canApprove && (
              <div
                className="flex items-start gap-2 rounded-lg border border-status-serious/30 bg-serious-surface p-3 text-xs leading-5 text-ink-2"
                role="status"
              >
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-status-serious" />
                This exact plan is not currently approvable. Refresh its status before taking
                another decision.
              </div>
            )}
            {(approve.isError || reject.isError) && (
              <ErrorState error={approve.error ?? reject.error} />
            )}
            {expired && (
              <div
                className="flex items-start gap-2 rounded-lg border border-warning-accent bg-warning-surface p-3 text-xs leading-5 text-brand-maroon dark:text-ink-2"
                role="status"
              >
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-status-warning" />
                <span>
                  This exact plan has expired and cannot be approved or replayed. Create a new
                  exact plan so its targets, evidence, and preconditions can be revalidated.
                </span>
              </div>
            )}
            {!pending && !expired && (
              <div className="flex items-start gap-2 rounded-lg border border-grid bg-page/30 p-3 text-xs text-ink-2">
                {["SUCCEEDED", "VERIFIED"].includes(displayStatus) ? (
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-status-good" />
                ) : (
                  <Info className="mt-0.5 h-4 w-4 shrink-0 text-status-info" />
                )}
                This request is no longer awaiting a decision. Its complete history remains
                available above.
              </div>
            )}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, Fingerprint, ShieldCheck, X, XCircle } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { apiGet, apiPost } from "../lib/api";
import type { ActionRequest, Row } from "../lib/types";
import { DataTable } from "./DataTable";
import { Badge, ErrorState, Skeleton, statusTone } from "./ui";

type ActionDetail = ActionRequest & {
  action_id: string;
  action_type: string;
  plan_hash: string;
  confirm_phrase: string;
  status: string;
  actions_enabled: boolean;
  targets?: Row[];
  items?: Row[];
  impact?: Row;
  rollback?: Row | string;
  verification?: Row | string;
  before_state?: unknown;
  after_state?: unknown;
  approvals?: Row[];
  events?: Row[];
};

function Detail({ label, value }: { label: string; value: unknown }) {
  if (value === undefined || value === null || value === "") return null;
  return (
    <div className="rounded-lg border border-grid bg-page/30 p-3">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-muted">{label}</p>
      <pre className="mt-1 whitespace-pre-wrap break-words font-sans text-xs leading-5 text-ink-2">
        {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
      </pre>
    </div>
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
  const [confirmation, setConfirmation] = useState("");
  const [reason, setReason] = useState("");
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const confirmId = useId();
  const previousFocus = useRef<HTMLElement | null>(
    document.activeElement instanceof HTMLElement ? document.activeElement : null,
  );
  const query = useQuery({
    queryKey: ["action-request", actionId],
    queryFn: () => apiGet<ActionDetail>(`/api/action-requests/${actionId}`),
    retry: false,
  });
  const approve = useMutation({
    mutationFn: (action: ActionDetail) =>
      apiPost<ActionDetail>(`/api/action-requests/${action.action_id}/approve`, {
        plan_hash: action.plan_hash,
        confirmation,
      }),
    onSuccess: () => {
      void query.refetch();
      onChanged?.();
    },
  });
  const reject = useMutation({
    mutationFn: (action: ActionDetail) =>
      apiPost<ActionDetail>(`/api/action-requests/${action.action_id}/reject`, {
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
  const items = action?.targets ?? action?.items ?? [];
  const pending = action?.status === "AWAITING_APPROVAL";
  const confirmationRequired = ["MEDIUM", "HIGH"].includes(
    String(action?.risk ?? "").toUpperCase(),
  );
  const phraseOk =
    !confirmationRequired ||
    (action !== undefined && confirmation === action.confirm_phrase);

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-3 backdrop-blur-sm"
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
            className="rounded-lg p-1.5 text-muted hover:bg-hairline"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {query.isPending && <Skeleton rows={7} />}
        {query.isError && <ErrorState error={query.error} />}
        {action && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2 rounded-xl border border-grid bg-page/30 p-3 text-xs">
              <Badge tone={statusTone(action.status)}>{action.status.replaceAll("_", " ")}</Badge>
              <Badge tone={statusTone(action.risk)}>{String(action.risk)} risk</Badge>
              <span className="font-medium text-ink">{action.action_type}</span>
              <span className="inline-flex min-w-0 items-center gap-1 font-mono text-[10px] text-muted">
                <Fingerprint className="h-3.5 w-3.5" />
                <span title={action.plan_hash}>{action.plan_hash.slice(0, 16)}…</span>
              </span>
              <span className="ml-auto text-muted">expires {String(action.expires_at)}</span>
            </div>

            {items.length > 0 && (
              <DataTable
                rows={items}
                pageSize={6}
                exportName={`action-${action.action_id}`}
                caption={`Exact targets for ${action.action_type}`}
              />
            )}

            <div className="grid gap-2 md:grid-cols-3">
              <Detail label="Impact" value={action.impact} />
              <Detail label="Rollback" value={action.rollback} />
              <Detail label="Verification" value={action.verification} />
            </div>

            {(action.events?.length ?? 0) > 0 && (
              <DataTable
                rows={action.events ?? []}
                pageSize={6}
                exportName={`action-${action.action_id}-events`}
                caption="Action audit events"
              />
            )}

            {pending && action.actions_enabled && (
              <div className="grid gap-3 rounded-xl border border-status-warning/30 bg-status-warning/5 p-3 md:grid-cols-2">
                <div>
                  <label htmlFor={confirmId} className="text-xs leading-5 text-ink-2">
                    Type{" "}
                    <code className="rounded bg-hairline px-1.5 py-0.5 font-mono text-ink">
                      {action.confirm_phrase}
                    </code>{" "}
                    to approve this exact hash.
                  </label>
                  <input
                    id={confirmId}
                    value={confirmation}
                    onChange={(event) => setConfirmation(event.target.value)}
                    className="mt-2 w-full rounded-lg border border-grid bg-page px-3 py-2 text-sm text-ink"
                    autoComplete="off"
                    spellCheck={false}
                  />
                  <button
                    type="button"
                    disabled={!phraseOk || approve.isPending}
                    onClick={() => approve.mutate(action)}
                    className="mt-2 inline-flex items-center gap-2 rounded-lg bg-status-critical px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
                  >
                    <ShieldCheck className="h-4 w-4" />
                    {approve.isPending ? "Approving…" : "Approve exact plan"}
                  </button>
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
                    className="mt-2 inline-flex items-center gap-2 rounded-lg border border-status-critical/40 px-3 py-2 text-sm font-medium text-status-critical hover:bg-status-critical/10 disabled:opacity-40"
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
            {(approve.isError || reject.isError) && (
              <ErrorState error={approve.error ?? reject.error} />
            )}
            {!pending && (
              <div className="flex items-start gap-2 rounded-lg border border-grid bg-page/30 p-3 text-xs text-ink-2">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-status-good" />
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

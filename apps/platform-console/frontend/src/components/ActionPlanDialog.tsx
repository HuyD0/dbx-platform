import { useMutation } from "@tanstack/react-query";
import { ShieldAlert, X } from "lucide-react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { apiPost } from "../lib/api";
import type { ApplyResponse, PlanResponse } from "../lib/types";
import { DataTable } from "./DataTable";
import { Badge, EmptyState, ErrorState, Skeleton } from "./ui";

/** The confirm gate, mirrored from the CLI: plan (dry-run) → typed confirm
 * phrase → apply. Plans are single-use and expire server-side; the dialog
 * shows a live countdown and disables Apply when the deployment gate is off. */
export function ActionPlanDialog({
  action,
  title,
  onClose,
}: {
  action: string;
  title: string;
  onClose: () => void;
}) {
  const [confirm, setConfirm] = useState("");
  const [now, setNow] = useState(() => Date.now());

  const plan = useMutation({
    mutationFn: () => apiPost<PlanResponse>(`/api/actions/${action}/plan`),
  });
  const apply = useMutation({
    mutationFn: (body: { plan_id: string; confirm: string }) =>
      apiPost<ApplyResponse>(`/api/actions/${action}/apply`, body),
  });

  useEffect(() => {
    plan.mutate();
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(tick);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [action]);

  const p = plan.data;
  const secondsLeft = p ? Math.max(0, Math.floor(p.expires_at * 1000 - now) / 1000) : 0;
  const phraseOk = p !== undefined && confirm === p.confirm_phrase;
  const applied = apply.data;

  // Portaled to <body>: the trigger sits inside .glass surfaces, whose
  // backdrop-filter makes them containing blocks for fixed descendants —
  // rendered inline, this fixed overlay would be trapped inside the card.
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className="glass-strong max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-2xl p-5 shadow-2xl">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
            <ShieldAlert className="h-4 w-4 text-status-serious" />
            {title}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 text-muted hover:bg-hairline"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {plan.isPending && <Skeleton rows={4} />}
        {plan.isError && <ErrorState error={plan.error} />}

        {p && !applied && (
          <>
            <p className="text-xs text-muted">
              Dry-run plan — nothing has changed yet. Plans are single-use and expire in{" "}
              {Math.floor(secondsLeft / 60)}m {Math.floor(secondsLeft % 60)}s.
            </p>
            <div className="my-3 flex flex-wrap gap-2">
              {Object.entries(p.summary).map(([k, v]) => (
                <Badge key={k} tone={k.includes("unchanged") || k.includes("untouched") ? "info" : "warning"}>
                  {k}: {v}
                </Badge>
              ))}
            </div>
            {p.items.length === 0 ? (
              <EmptyState message="Nothing to do — the dry-run found no items." />
            ) : (
              <div className="max-h-64 overflow-y-auto rounded-lg border border-grid">
                <DataTable rows={p.items} />
              </div>
            )}

            {p.items.length > 0 && p.actions_enabled && (
              <div className="mt-4 space-y-2">
                <label className="block text-xs text-ink-2" htmlFor="confirm-input">
                  Type <code className="rounded bg-hairline px-1.5 py-0.5 font-mono text-ink">{p.confirm_phrase}</code> to
                  enable Apply:
                </label>
                <div className="flex gap-2">
                  <input
                    id="confirm-input"
                    value={confirm}
                    onChange={(e) => setConfirm(e.target.value)}
                    className="w-full rounded-lg border border-grid bg-page px-3 py-1.5 text-sm text-ink outline-none focus:border-accent"
                    placeholder={p.confirm_phrase}
                    autoComplete="off"
                  />
                  <button
                    type="button"
                    disabled={!phraseOk || secondsLeft <= 0 || apply.isPending}
                    onClick={() => apply.mutate({ plan_id: p.plan_id, confirm })}
                    className="shrink-0 rounded-lg bg-status-critical px-4 py-1.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {apply.isPending ? "Applying…" : "Apply"}
                  </button>
                </div>
              </div>
            )}
            {p.items.length > 0 && !p.actions_enabled && (
              <p className="mt-4 rounded-lg border border-grid bg-hairline/40 px-3 py-2 text-xs text-ink-2">
                Remediation actions are disabled for this deployment (report-only). Enable
                them by setting <code>DBX_PLATFORM_CONSOLE_ACTIONS=true</code> in{" "}
                <code>app.yaml</code> — a git-reviewed change; see docs/runbook.md for the
                required grants.
              </p>
            )}
            {apply.isError && (
              <div className="mt-3">
                <ErrorState error={apply.error} />
              </div>
            )}
          </>
        )}

        {applied && (
          <div className="space-y-2">
            <Badge tone="good">applied {applied.applied.length} change(s)</Badge>
            <ul className="list-disc space-y-1 pl-5 text-xs text-ink-2">
              {applied.applied.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
            <button
              type="button"
              onClick={onClose}
              className="mt-2 rounded-lg border border-grid px-4 py-1.5 text-sm text-ink hover:bg-hairline"
            >
              Done
            </button>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

/** Entry-point button placed on findings pages. */
export function PlanActionButton({ action, label }: { action: string; label: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="rounded-lg border border-grid px-3 py-1 text-xs font-medium text-ink hover:bg-hairline"
      >
        {label}
      </button>
      {open && (
        <ActionPlanDialog action={action} title={label} onClose={() => setOpen(false)} />
      )}
    </>
  );
}

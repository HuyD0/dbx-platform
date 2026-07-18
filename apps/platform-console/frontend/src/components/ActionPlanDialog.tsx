import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, Clock3, Fingerprint, ShieldAlert, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { apiPost, isUnavailable } from "../lib/api";
import type { ApplyResponse, Envelope, PlanResponse } from "../lib/types";
import { DataTable } from "./DataTable";
import { Badge, EmptyState, ErrorState, Skeleton } from "./ui";

function unwrapPlan(response: PlanResponse | Envelope<PlanResponse>): PlanResponse {
  return "data" in response ? response.data : response;
}

function unwrapApproval(response: ApplyResponse | Envelope<ApplyResponse>): ApplyResponse {
  return "data" in response ? response.data : response;
}

function expiresAtMs(value: number | string): number {
  if (typeof value === "number") return value > 10_000_000_000 ? value : value * 1000;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function DetailBlock({ label, value }: { label: string; value: unknown }) {
  if (value === undefined || value === null || value === "") return null;
  const rendered = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return (
    <div className="rounded-lg border border-grid bg-page/30 p-3">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-muted">{label}</div>
      <pre className="mt-1 whitespace-pre-wrap break-words font-sans text-xs leading-5 text-ink-2">
        {rendered}
      </pre>
    </div>
  );
}

/** Human gate for every state-changing operation. The component prefers the
 * durable action-request API and falls back to the legacy single-use planner
 * only for existing remediations while the backend migration is in flight. */
export function ActionPlanDialog({
  action,
  title,
  parameters = {},
  allowLegacy = true,
  onClose,
}: {
  action: string;
  title: string;
  parameters?: Record<string, unknown>;
  allowLegacy?: boolean;
  onClose: () => void;
}) {
  const [confirm, setConfirm] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const confirmId = useId();
  const previousFocus = useRef<HTMLElement | null>(
    document.activeElement instanceof HTMLElement ? document.activeElement : null,
  );

  const plan = useMutation({
    mutationFn: async () => {
      try {
        const response = await apiPost<PlanResponse | Envelope<PlanResponse>>(
          "/api/action-requests/plan",
          { action, parameters },
        );
        return { plan: unwrapPlan(response), legacy: false };
      } catch (error) {
        if (!allowLegacy || !isUnavailable(error)) throw error;
        const response = await apiPost<PlanResponse>(`/api/actions/${action}/plan`, parameters);
        // Compatibility planning stays read-only. Legacy apply lacks the
        // durable approver identity, immutable audit and revalidation contract,
        // so the UI fails closed until the generic service is available.
        return { plan: { ...response, actions_enabled: false }, legacy: true };
      }
    },
  });
  const approve = useMutation({
    mutationFn: async (approvedPlan: PlanResponse) => {
      const response = await apiPost<ApplyResponse | Envelope<ApplyResponse>>(
        `/api/action-requests/${approvedPlan.plan_id}/approve`,
        {
          plan_hash: approvedPlan.plan_hash,
          confirm,
        },
      );
      return unwrapApproval(response);
    },
  });

  useEffect(() => {
    plan.mutate();
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
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
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => !element.hasAttribute("hidden"));
      if (focusable.length === 0) {
        event.preventDefault();
        dialogRef.current.focus();
        return;
      }
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
      window.clearInterval(tick);
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = originalOverflow;
      previousFocus.current?.focus();
    };
    // This is deliberately one dialog lifecycle; the action cannot change in place.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const result = plan.data;
  const p = result?.plan;
  const secondsLeft = p ? Math.max(0, Math.floor((expiresAtMs(p.expires_at) - now) / 1000)) : 0;
  const phraseOk = p !== undefined && confirm === p.confirm_phrase;
  const approved = approve.data;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-3 backdrop-blur-sm sm:p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="glass-strong max-h-[92vh] w-full max-w-3xl overflow-y-auto rounded-2xl p-4 shadow-2xl sm:p-5"
      >
        <div className="mb-3 flex items-start justify-between gap-3">
          <div>
            <p className="mb-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-status-serious">
              Human approval required
            </p>
            <h2 id={titleId} className="flex items-center gap-2 text-base font-semibold text-ink">
              <ShieldAlert className="h-4 w-4 text-status-serious" />
              {title}
            </h2>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close approval dialog"
            className="rounded-lg p-1.5 text-muted hover:bg-hairline"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {plan.isPending && <Skeleton rows={5} />}
        {plan.isError && <ErrorState error={plan.error} />}

        {p && !approved && (
          <>
            <div className="flex flex-wrap items-center gap-2 rounded-xl border border-grid bg-hairline/20 p-3 text-xs text-muted">
              <Clock3 className="h-4 w-4 text-accent" />
              <span>
                Exact plan · single use · expires in{" "}
                <strong className="font-semibold text-ink">
                  {Math.floor(secondsLeft / 60)}m {Math.floor(secondsLeft % 60)}s
                </strong>
              </span>
              {p.plan_hash && (
                <span
                  className="inline-flex min-w-0 items-center gap-1 font-mono text-[10px]"
                  title={p.plan_hash}
                >
                  <Fingerprint className="h-3.5 w-3.5 shrink-0" />
                  {p.plan_hash.slice(0, 12)}…
                </span>
              )}
              {p.risk && <Badge tone={p.risk === "high" ? "critical" : "warning"}>{p.risk} risk</Badge>}
            </div>

            <div className="my-3 flex flex-wrap gap-2">
              {Object.entries(p.summary ?? {}).map(([key, value]) => (
                <Badge
                  key={key}
                  tone={key.includes("unchanged") || key.includes("untouched") ? "info" : "warning"}
                >
                  {key.replaceAll("_", " ")}: {value}
                </Badge>
              ))}
            </div>

            {p.items.length === 0 ? (
              <EmptyState message="The planner found no resources to change." />
            ) : (
              <DataTable
                rows={p.items}
                pageSize={6}
                exportName={`plan-${action}`}
                caption={`Exact resources in ${title}`}
              />
            )}

            <div className="mt-3 grid gap-2 sm:grid-cols-3">
              <DetailBlock label="Impact" value={p.impact} />
              <DetailBlock label="Rollback" value={p.rollback} />
              <DetailBlock label="Verification" value={p.verification} />
            </div>

            {p.items.length > 0 && p.actions_enabled && (
              <div className="mt-4 rounded-xl border border-status-serious/30 bg-status-serious/5 p-3">
                <label className="block text-xs leading-5 text-ink-2" htmlFor={confirmId}>
                  Type{" "}
                  <code className="rounded bg-hairline px-1.5 py-0.5 font-mono text-ink">
                    {p.confirm_phrase}
                  </code>{" "}
                  to approve this exact plan:
                </label>
                <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                  <input
                    id={confirmId}
                    value={confirm}
                    onChange={(event) => setConfirm(event.target.value)}
                    className="w-full rounded-lg border border-grid bg-page px-3 py-2 text-sm text-ink outline-none focus:border-accent"
                    placeholder={p.confirm_phrase}
                    autoComplete="off"
                    spellCheck={false}
                  />
                  <button
                    type="button"
                    disabled={!phraseOk || secondsLeft <= 0 || approve.isPending}
                    onClick={() => approve.mutate(p)}
                    className="shrink-0 rounded-lg bg-status-critical px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {approve.isPending ? "Approving…" : "Approve exact plan"}
                  </button>
                </div>
              </div>
            )}
            {p.items.length > 0 && !p.actions_enabled && (
              <p className="mt-4 rounded-lg border border-grid bg-hairline/40 px-3 py-2 text-xs leading-5 text-ink-2">
                This deployment is proposal-only. The plan can be inspected and exported, but
                execution remains disabled until the audited executor and approver group are
                configured.
              </p>
            )}
            {approve.isError && (
              <div className="mt-3">
                <ErrorState error={approve.error} />
              </div>
            )}
          </>
        )}

        {approved && (
          <div className="space-y-3">
            <div className="flex items-start gap-3 rounded-xl border border-status-good/30 bg-status-good/5 p-4">
              <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-status-good" />
              <div>
                <h3 className="text-sm font-semibold text-ink">
                  {approved.status ? approved.status.replaceAll("_", " ") : "Plan accepted"}
                </h3>
                <p className="mt-1 text-xs leading-5 text-ink-2">
                  The approval is recorded against the exact plan. Execution and verification
                  progress will appear in Action Center.
                </p>
              </div>
            </div>
            {(approved.applied ?? []).length > 0 && (
              <ul className="list-disc space-y-1 pl-5 text-xs text-ink-2">
                {(approved.applied ?? []).map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            )}
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-grid px-4 py-2 text-sm text-ink hover:bg-hairline"
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

export function PlanActionButton({
  action,
  label,
  parameters,
  allowLegacy = true,
  tone = "default",
}: {
  action: string;
  label: string;
  parameters?: Record<string, unknown>;
  allowLegacy?: boolean;
  tone?: "default" | "danger" | "primary";
}) {
  const [open, setOpen] = useState(false);
  const classes = {
    default: "border-grid text-ink hover:bg-hairline",
    danger: "border-status-critical/40 text-status-critical hover:bg-status-critical/10",
    primary: "border-accent bg-accent text-white hover:brightness-110",
  };
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={`rounded-lg border px-3 py-1.5 text-xs font-medium ${classes[tone]}`}
      >
        {label}
      </button>
      {open && (
        <ActionPlanDialog
          action={action}
          title={label}
          parameters={parameters}
          allowLegacy={allowLegacy}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

import { useMutation } from "@tanstack/react-query";
import {
  Check,
  CheckCircle2,
  ChevronDown,
  Circle,
  Clock3,
  Code2,
  Fingerprint,
  Play,
  ShieldAlert,
  X,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { apiPost, isUnavailable } from "../lib/api";
import type { ApplyResponse, Envelope, PlanResponse } from "../lib/types";
import { DataTable } from "./DataTable";
import { Badge, EmptyState, ErrorState, HelpTip, Skeleton } from "./ui";

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

function recordValue(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function detailSummary(label: string, value: unknown): string {
  const record = recordValue(value);
  if (typeof value === "string") return value;
  if (label === "Impact") {
    const summary = recordValue(record?.summary);
    const count = Number(record?.target_count ?? 0);
    if (summary?.run) return `Starts ${String(summary.run)} new job run.`;
    if (count) return `${count} resource${count === 1 ? "" : "s"} may change.`;
  }
  if (label === "Rollback" && record?.description) return String(record.description);
  if (label === "Verification" && record?.strategy) return String(record.strategy);
  return "See technical details below.";
}

function DetailBlock({ label, value, help }: { label: string; value: unknown; help: string }) {
  if (value === undefined || value === null || value === "") return null;
  return (
    <div className="rounded-lg border border-grid bg-page/30 p-3">
      <div className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide text-muted">
        {label}
        <HelpTip label={`About ${label.toLowerCase()}`}>{help}</HelpTip>
      </div>
      <p className="mt-1 text-xs leading-5 text-ink-2">{detailSummary(label, value)}</p>
    </div>
  );
}

type PipelineStepState = "completed" | "executing" | "pending";

function PipelineTimeline({
  planning,
  planReady,
  approvalAvailable,
  confirming,
  approved,
}: {
  planning: boolean;
  planReady: boolean;
  approvalAvailable: boolean;
  confirming: boolean;
  approved: boolean;
}) {
  const steps: Array<{ label: string; detail: string; state: PipelineStepState }> = [
    {
      label: "Generate immutable plan",
      detail: "Capture exact targets and intended state.",
      state: planReady ? "completed" : planning ? "executing" : "pending",
    },
    {
      label: "Fingerprint scope",
      detail: "Bind the payload to its single-use hash.",
      state: planReady ? "completed" : "pending",
    },
    {
      label: "Human risk acceptance",
      detail: approved
        ? "Approval recorded for the exact plan."
        : planReady && !approvalAvailable
          ? "Approval remains closed while execution is disabled."
          : confirming
            ? "A separate explicit confirmation is required."
            : "Review impact before opening the confirmation gate.",
      state: approved ? "completed" : planReady && approvalAvailable ? "executing" : "pending",
    },
    {
      label: "Dedicated executor handoff",
      detail: approved
        ? "The approved request is ready for its least-privileged executor."
        : "Unavailable until approval is recorded.",
      state: approved ? "executing" : "pending",
    },
    {
      label: "Revalidate and verify",
      detail: "Fail closed on drift, expiry, replay, or identity mismatch.",
      state: "pending",
    },
  ];

  return (
    <ol className="mt-4" aria-label="Approval and deployment pipeline">
      {steps.map((step, index) => (
        <li
          key={step.label}
          aria-current={step.state === "executing" ? "step" : undefined}
          className={`relative grid grid-cols-[1.5rem_1fr] gap-2.5 ${
            index === steps.length - 1 ? "" : "pb-5"
          }`}
        >
          {index < steps.length - 1 && (
            <span
              aria-hidden="true"
              className="absolute bottom-0 left-[0.6875rem] top-6 w-px bg-[#E4D7DB]"
            />
          )}
          <span
            className={`relative z-[1] flex h-6 w-6 items-center justify-center rounded-full border bg-white ${
              step.state === "completed"
                ? "border-[#72BF44]"
                : step.state === "executing"
                  ? "border-[#FFCD67]"
                  : "border-[#E4D7DB]"
            }`}
          >
            {step.state === "completed" ? (
              <Check className="h-3.5 w-3.5 stroke-[3] text-[#72BF44]" />
            ) : step.state === "executing" ? (
              <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-[#FFCD67]" />
            ) : (
              <Circle className="h-2.5 w-2.5 text-[#B79AA3]" />
            )}
          </span>
          <div className="min-w-0 pt-0.5">
            <p className="text-xs font-semibold text-[#240B15]">{step.label}</p>
            <p className="mt-0.5 text-[10px] leading-4 text-[#806A72]">{step.detail}</p>
            <span className="sr-only">
              {step.state === "completed"
                ? "Completed"
                : step.state === "executing"
                  ? "Active"
                  : "Pending"}
            </span>
          </div>
        </li>
      ))}
    </ol>
  );
}

type DiffTone = "file" | "hunk" | "addition" | "removal" | "context";

function diffPreviewLines(plan: PlanResponse): Array<{ tone: DiffTone; text: string }> {
  const targetLines: Array<{ tone: DiffTone; text: string }> = plan.items
    .slice(0, 4)
    .map((item, index) => ({
      tone: "addition",
      text: `+  target_${index + 1} = ${JSON.stringify(item)}`,
    }));
  if (plan.items.length > 4) {
    targetLines.push({
      tone: "context",
      text: `   # ${plan.items.length - 4} additional exact target${plan.items.length - 4 === 1 ? "" : "s"} in export`,
    });
  }
  return [
    { tone: "file", text: "--- current/workspace-state" },
    { tone: "file", text: "+++ proposed/immutable-action-plan" },
    { tone: "hunk", text: `@@ resource \"dbx_platform_action\" \"${plan.action}\" @@` },
    { tone: "removal", text: "-  approval_state = null" },
    { tone: "addition", text: `+  action         = ${JSON.stringify(plan.action)}` },
    { tone: "addition", text: `+  plan_id        = ${JSON.stringify(plan.plan_id)}` },
    { tone: "addition", text: `+  target_count   = ${plan.items.length}` },
    { tone: "addition", text: `+  risk           = ${JSON.stringify(plan.risk ?? "medium")}` },
    ...(plan.plan_hash
      ? [
          {
            tone: "addition" as const,
            text: `+  plan_sha256    = ${JSON.stringify(plan.plan_hash)}`,
          },
        ]
      : []),
    ...targetLines,
    {
      tone: "context",
      text: `   execution_enabled            = ${plan.actions_enabled}`,
    },
    {
      tone: "context",
      text: `   revalidate_before_execution  = ${plan.actions_enabled}`,
    },
    {
      tone: "context",
      text: `   append_only_verification     = ${plan.actions_enabled}`,
    },
  ];
}

function PlanDiffPreview({ plan }: { plan: PlanResponse }) {
  const toneClasses: Record<DiffTone, string> = {
    file: "text-[#B79AA3]",
    hunk: "text-[#FFCD67]",
    addition: "text-[#00AAAD]",
    removal: "text-[#FF8DA0]",
    context: "text-[#FBF7F8]",
  };
  return (
    <figure className="mt-3 overflow-hidden rounded-xl border border-[#E4D7DB] bg-[#240B15]">
      <figcaption className="flex items-center justify-between gap-3 border-b border-white/15 px-3 py-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-white">
        <span className="inline-flex items-center gap-1.5">
          <Code2 className="h-3.5 w-3.5 text-[#FFCD67]" />
          IaC diff preview
        </span>
        <span className="font-normal normal-case tracking-normal text-[#B79AA3]">
          exact plan payload
        </span>
      </figcaption>
      <pre
        className="max-h-64 overflow-auto p-3 text-[10px] leading-5"
        aria-label="Syntax-highlighted immutable plan diff"
      >
        <code>
          {diffPreviewLines(plan).map((line, index) => (
            <span key={`${line.text}-${index}`} className={`block ${toneClasses[line.tone]}`}>
              {line.text}
            </span>
          ))}
        </code>
      </pre>
    </figure>
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
  const [confirming, setConfirming] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const approvalTriggerRef = useRef<HTMLButtonElement>(null);
  const acceptRiskRef = useRef<HTMLButtonElement>(null);
  const doneRef = useRef<HTMLButtonElement>(null);
  const confirmingRef = useRef(false);
  const confirmationReturnTargetRef = useRef<"trigger" | "close" | null>(null);
  const titleId = useId();
  const confirmationTitleId = useId();
  const confirmationDescriptionId = useId();
  const confirmationWarningId = useId();
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
        },
      );
      return unwrapApproval(response);
    },
    onSuccess: () => {
      confirmingRef.current = false;
      setConfirming(false);
    },
  });

  useEffect(() => {
    confirmingRef.current = confirming;
    if (confirming) {
      window.requestAnimationFrame(() => acceptRiskRef.current?.focus());
      return;
    }
    const returnTarget = confirmationReturnTargetRef.current;
    confirmationReturnTargetRef.current = null;
    if (returnTarget === "trigger") {
      window.requestAnimationFrame(() => approvalTriggerRef.current?.focus());
    } else if (returnTarget === "close") {
      window.requestAnimationFrame(() => closeRef.current?.focus());
    }
  }, [confirming]);

  useEffect(() => {
    plan.mutate();
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.requestAnimationFrame(() => closeRef.current?.focus());

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        if (confirmingRef.current) {
          confirmationReturnTargetRef.current = "trigger";
          confirmingRef.current = false;
          setConfirming(false);
          return;
        }
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
  const approved = approve.data;
  const target = p?.items[0];
  const isJobRun = p?.action === "run-job";
  const targetName = String(target?.name ?? "this job");
  const risk = String(p?.risk ?? "medium").toLowerCase();
  const riskTone = risk === "high" ? "critical" : risk === "low" ? "info" : "warning";

  useEffect(() => {
    if (
      !confirming ||
      (p !== undefined && p.actions_enabled && p.items.length > 0 && secondsLeft > 0)
    ) {
      return;
    }
    confirmationReturnTargetRef.current = "close";
    confirmingRef.current = false;
    setConfirming(false);
  }, [confirming, p, secondsLeft]);

  useEffect(() => {
    if (approved) window.requestAnimationFrame(() => doneRef.current?.focus());
  }, [approved]);

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
        className="glass-strong max-h-[92vh] w-full max-w-6xl overflow-y-auto rounded-2xl p-4 shadow-2xl sm:p-5"
      >
        <div className="mb-3 flex items-start justify-between gap-3">
          <div>
            <p className="mb-1 border-l-2 border-[#F00037] pl-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-[#8B001F]">
              Human approval required
            </p>
            <h2 id={titleId} className="flex items-center gap-2 text-base font-semibold text-ink">
              <ShieldAlert className="h-4 w-4 text-[#F00037]" />
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

        <div className="grid gap-4 lg:grid-cols-[16rem_minmax(0,1fr)]">
          <aside className="self-start rounded-xl border border-[#E4D7DB] bg-[#FBF7F8] p-4 lg:sticky lg:top-0">
            <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[#8B001F]">
              Deployment pipeline
            </p>
            <h3 className="mt-1 text-sm font-semibold text-[#240B15]">Approval control path</h3>
            <p className="mt-1 text-[10px] leading-4 text-[#806A72]">
              Every stage binds to one expiring, immutable plan. No stage grants authority to the
              assistant.
            </p>
            <PipelineTimeline
              planning={plan.isPending}
              planReady={Boolean(p)}
              approvalAvailable={Boolean(p?.actions_enabled && p.items.length > 0)}
              confirming={confirming}
              approved={Boolean(approved)}
            />
          </aside>

          <div className="min-w-0">
            {plan.isPending && <Skeleton rows={5} />}
            {plan.isError && <ErrorState error={plan.error} />}
            {p && <PlanDiffPreview plan={p} />}

            {p && !approved && (
              <>
                <div className="mt-3 rounded-xl border border-accent/25 bg-accent/5 p-4">
                  <div className="flex items-start gap-3">
                    <span className="mt-0.5 rounded-lg bg-accent/10 p-2 text-accent">
                      <Play className="h-4 w-4" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <h3 className="text-sm font-semibold text-ink">
                        {isJobRun ? `Run ${targetName} once?` : `Approve “${title}”?`}
                      </h3>
                      <p className="mt-1 text-xs leading-5 text-ink-2">
                        {isJobRun
                          ? "This starts one new run now. It does not change the job or its schedule."
                          : `This applies the reviewed action to ${p.items.length} exact target${p.items.length === 1 ? "" : "s"}.`}
                      </p>
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-grid/70 pt-3 text-xs text-muted">
                    <Clock3 className="h-4 w-4 text-accent" />
                    <span>
                      Available for{" "}
                      <strong className="font-semibold text-ink">
                        {Math.floor(secondsLeft / 60)}m {Math.floor(secondsLeft % 60)}s
                      </strong>
                    </span>
                    <HelpTip label="Why does this approval expire?">
                      The short expiry prevents an old approval from being reused after the target
                      changes. The app checks the target again immediately before execution.
                    </HelpTip>
                    <span className="ml-auto inline-flex items-center gap-1">
                      <Badge tone={riskTone}>{risk} risk</Badge>
                      <HelpTip label={`What does ${risk} risk mean?`}>
                        Risk describes the possible operational impact. Every approval uses a
                        separate confirmation step, plus expiry, revalidation, and an exact plan
                        hash.
                      </HelpTip>
                    </span>
                  </div>
                </div>

                <div className="my-3 flex flex-wrap gap-2" aria-label="Plan summary">
                  {Object.entries(p.summary ?? {}).map(([key, value]) => (
                    <Badge
                      key={key}
                      tone={
                        key.includes("unchanged") || key.includes("untouched") ? "info" : "warning"
                      }
                    >
                      {key.replaceAll("_", " ")}: {value}
                    </Badge>
                  ))}
                </div>

                {p.items.length === 0 && <EmptyState message="There is nothing to approve." />}

                <div className="mt-3 grid gap-2 sm:grid-cols-3">
                  <DetailBlock
                    label="Impact"
                    value={p.impact}
                    help="What this action will start or change."
                  />
                  <DetailBlock
                    label="Rollback"
                    value={p.rollback}
                    help="Whether Mission Control can automatically undo the action."
                  />
                  <DetailBlock
                    label="Verification"
                    value={p.verification}
                    help="How Mission Control checks and records the result after execution."
                  />
                </div>

                {p.items.length > 0 && (
                  <details className="mt-3 rounded-lg border border-grid bg-page/30">
                    <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs font-medium text-ink hover:bg-hairline">
                      <ChevronDown className="h-3.5 w-3.5 text-muted" />
                      Technical details
                      <span className="font-normal text-muted">
                        Exact target, IDs, hashes, and export
                      </span>
                    </summary>
                    <div className="space-y-3 border-t border-grid p-3">
                      {p.plan_hash && (
                        <p className="flex min-w-0 items-center gap-1 text-[10px] text-muted">
                          <Fingerprint className="h-3.5 w-3.5 shrink-0" />
                          Plan fingerprint: <code className="break-all">{p.plan_hash}</code>
                        </p>
                      )}
                      <DataTable
                        rows={p.items}
                        pageSize={6}
                        exportName={`plan-${action}`}
                        caption={`Exact resources in ${title}`}
                      />
                    </div>
                  </details>
                )}

                {p.items.length > 0 && p.actions_enabled && (
                  <div className="mt-4">
                    {!confirming ? (
                      <div className="flex justify-end">
                        <button
                          ref={approvalTriggerRef}
                          type="button"
                          disabled={secondsLeft <= 0 || approve.isPending}
                          onClick={() => {
                            confirmingRef.current = true;
                            setConfirming(true);
                          }}
                          className="shrink-0 rounded-lg bg-[#8B001F] px-4 py-2 text-sm font-semibold text-white hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          {isJobRun ? "Approve and run once" : "Approve action"}
                        </button>
                      </div>
                    ) : (
                      <div
                        role="alertdialog"
                        aria-labelledby={confirmationTitleId}
                        aria-describedby={`${confirmationDescriptionId} ${confirmationWarningId}`}
                        className="rounded-xl border border-status-serious/30 bg-status-serious/5 p-3"
                      >
                        <h4 id={confirmationTitleId} className="text-sm font-semibold text-ink">
                          Confirm risk acceptance
                        </h4>
                        <p
                          id={confirmationDescriptionId}
                          className="mt-1 text-xs leading-5 text-ink-2"
                        >
                          {isJobRun
                            ? `Start one run of ${targetName} now?`
                            : `Approve this exact ${risk}-risk plan for ${p.items.length} target${p.items.length === 1 ? "" : "s"}?`}
                        </p>
                        <p
                          id={confirmationWarningId}
                          className="mt-2 text-[11px] font-medium leading-5 text-[#8B001F]"
                        >
                          Your next click records approval for this hash and authorizes the
                          dedicated executor to revalidate the target before acting.
                        </p>
                        <div className="mt-3 flex justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => {
                              confirmationReturnTargetRef.current = "trigger";
                              confirmingRef.current = false;
                              setConfirming(false);
                            }}
                            className="rounded-lg border border-grid px-4 py-2 text-sm font-medium text-ink hover:bg-hairline"
                          >
                            Back
                          </button>
                          <button
                            ref={acceptRiskRef}
                            type="button"
                            disabled={secondsLeft <= 0 || approve.isPending}
                            onClick={() => approve.mutate(p)}
                            className="min-h-12 shrink-0 rounded-lg bg-[#F00037] px-5 py-2 text-xl font-bold leading-6 text-white shadow-lg shadow-[#F00037]/20 hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none"
                          >
                            {approve.isPending ? "Recording approval…" : "Accept Risk & Execute"}
                          </button>
                        </div>
                      </div>
                    )}
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
                  ref={doneRef}
                  type="button"
                  onClick={onClose}
                  className="rounded-lg border border-grid px-4 py-2 text-sm text-ink hover:bg-hairline"
                >
                  Done
                </button>
              </div>
            )}
          </div>
        </div>
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

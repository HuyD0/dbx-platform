import { X } from "lucide-react";
import { FormEvent, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ActionPlanDialog } from "./ActionPlanDialog";

type BudgetDraft = {
  scope_type: "workspace" | "provider" | "team" | "use_case";
  scope_value: string;
  cost_basis: "AZURE_ACTUAL" | "DATABRICKS_LIST" | "PROVIDER_ESTIMATE";
  month: string;
  currency: string;
  amount: string;
  warning_threshold_pct: string;
  critical_threshold_pct: string;
};

function currentMonth(): string {
  return new Date().toISOString().slice(0, 7);
}

const INITIAL: BudgetDraft = {
  scope_type: "workspace",
  scope_value: "all",
  cost_basis: "AZURE_ACTUAL",
  month: currentMonth(),
  currency: "USD",
  amount: "",
  warning_threshold_pct: "80",
  critical_threshold_pct: "100",
};

function BudgetForm({
  label,
  onClose,
  onPlan,
}: {
  label: string;
  onClose: () => void;
  onPlan: (parameters: Record<string, unknown>) => void;
}) {
  const [draft, setDraft] = useState<BudgetDraft>(INITIAL);
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const amountRef = useRef<HTMLInputElement>(null);
  const titleId = useId();
  const previousFocus = useRef<HTMLElement | null>(
    document.activeElement instanceof HTMLElement ? document.activeElement : null,
  );

  useEffect(() => {
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.requestAnimationFrame(() => amountRef.current?.focus());
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
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

  const update = <K extends keyof BudgetDraft>(key: K, value: BudgetDraft[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    onPlan({
      scope_type: draft.scope_type,
      scope_value: draft.scope_value.trim(),
      cost_basis: draft.cost_basis,
      month: draft.month,
      currency: draft.currency.trim().toUpperCase(),
      amount: Number(draft.amount),
      warning_threshold_pct: Number(draft.warning_threshold_pct),
      critical_threshold_pct: Number(draft.critical_threshold_pct),
    });
  };

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
        className="glass-strong max-h-[92vh] w-full max-w-xl overflow-y-auto rounded-2xl p-4 shadow-2xl sm:p-5"
      >
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <p className="mb-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
              Budget proposal
            </p>
            <h2 id={titleId} className="text-base font-semibold text-ink">
              {label}
            </h2>
            <p className="mt-1 text-xs leading-5 text-muted">
              Choose one cost basis and currency. The next step creates an immutable,
              expiring plan for human approval.
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close budget form"
            className="rounded-lg p-1.5 text-muted hover:bg-hairline"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <form className="grid gap-3 sm:grid-cols-2" onSubmit={submit}>
          <label className="text-xs text-ink-2">
            Scope
            <select
              value={draft.scope_type}
              onChange={(event) =>
                update("scope_type", event.target.value as BudgetDraft["scope_type"])
              }
              className="mt-1 w-full rounded-lg border border-grid bg-page px-3 py-2 text-sm text-ink"
            >
              <option value="workspace">Workspace</option>
              <option value="provider">Provider</option>
              <option value="team">Team</option>
              <option value="use_case">Use case</option>
            </select>
          </label>
          <label className="text-xs text-ink-2">
            Scope value
            <input
              required
              value={draft.scope_value}
              onChange={(event) => update("scope_value", event.target.value)}
              className="mt-1 w-full rounded-lg border border-grid bg-page px-3 py-2 text-sm text-ink"
              placeholder="all or an exact allocation value"
            />
          </label>
          <label className="text-xs text-ink-2">
            Cost basis
            <select
              value={draft.cost_basis}
              onChange={(event) =>
                update("cost_basis", event.target.value as BudgetDraft["cost_basis"])
              }
              className="mt-1 w-full rounded-lg border border-grid bg-page px-3 py-2 text-sm text-ink"
            >
              <option value="AZURE_ACTUAL">Azure actual</option>
              <option value="DATABRICKS_LIST">Databricks list</option>
              <option value="PROVIDER_ESTIMATE">Provider estimate</option>
            </select>
          </label>
          <label className="text-xs text-ink-2">
            Month
            <input
              required
              type="month"
              min={currentMonth()}
              value={draft.month}
              onChange={(event) => update("month", event.target.value)}
              className="mt-1 w-full rounded-lg border border-grid bg-page px-3 py-2 text-sm text-ink"
            />
          </label>
          <label className="text-xs text-ink-2">
            Amount
            <input
              ref={amountRef}
              required
              type="number"
              min="0.01"
              step="0.01"
              value={draft.amount}
              onChange={(event) => update("amount", event.target.value)}
              className="mt-1 w-full rounded-lg border border-grid bg-page px-3 py-2 text-sm text-ink"
              placeholder="1000.00"
            />
          </label>
          <label className="text-xs text-ink-2">
            Currency
            <input
              required
              minLength={3}
              maxLength={3}
              pattern="[A-Za-z]{3}"
              value={draft.currency}
              onChange={(event) => update("currency", event.target.value)}
              className="mt-1 w-full rounded-lg border border-grid bg-page px-3 py-2 text-sm uppercase text-ink"
            />
          </label>
          <label className="text-xs text-ink-2">
            Warning at %
            <input
              required
              type="number"
              min="0"
              max="100"
              step="1"
              value={draft.warning_threshold_pct}
              onChange={(event) => update("warning_threshold_pct", event.target.value)}
              className="mt-1 w-full rounded-lg border border-grid bg-page px-3 py-2 text-sm text-ink"
            />
          </label>
          <label className="text-xs text-ink-2">
            Critical at %
            <input
              required
              type="number"
              min="0"
              max="100"
              step="1"
              value={draft.critical_threshold_pct}
              onChange={(event) => update("critical_threshold_pct", event.target.value)}
              className="mt-1 w-full rounded-lg border border-grid bg-page px-3 py-2 text-sm text-ink"
            />
          </label>
          <div className="flex justify-end gap-2 border-t border-grid pt-3 sm:col-span-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-grid px-3 py-2 text-sm text-ink hover:bg-hairline"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="rounded-lg border border-accent bg-accent px-3 py-2 text-sm font-medium text-white hover:brightness-110"
            >
              Preview exact plan
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  );
}

export function BudgetPlanButton({ label }: { label: string }) {
  const [stage, setStage] = useState<"closed" | "form" | "plan">("closed");
  const [parameters, setParameters] = useState<Record<string, unknown> | null>(null);
  return (
    <>
      <button
        type="button"
        onClick={() => setStage("form")}
        className="rounded-lg border border-grid px-3 py-1.5 text-xs font-medium text-ink hover:bg-hairline"
      >
        {label}
      </button>
      {stage === "form" && (
        <BudgetForm
          label={label}
          onClose={() => setStage("closed")}
          onPlan={(value) => {
            setParameters(value);
            setStage("plan");
          }}
        />
      )}
      {stage === "plan" && parameters && (
        <ActionPlanDialog
          action="configure-budget"
          title={label}
          parameters={parameters}
          allowLegacy={false}
          onClose={() => setStage("closed")}
        />
      )}
    </>
  );
}

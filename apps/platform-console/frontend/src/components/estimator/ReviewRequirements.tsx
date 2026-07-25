import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { PlannerWizardShell } from "./PlannerWizardShell";

const NUMERIC_FIELDS: { key: string; label: string; hint: string }[] = [
  { key: "monthly_requests", label: "Requests per month", hint: "Production traffic" },
  { key: "monthly_active_users", label: "People per month", hint: "0 = not specified" },
  { key: "avg_input_tokens", label: "Typical request size", hint: "0 = pattern default" },
  { key: "avg_output_tokens", label: "Typical answer size", hint: "0 = pattern default" },
  { key: "agent_steps", label: "Steps per task", hint: "0 = pattern default" },
  { key: "corpus_gb", label: "Document collection (GB)", hint: "0 = pattern default" },
  { key: "corpus_growth_pct_monthly", label: "Document growth (%/month)", hint: "" },
  { key: "peak_rps", label: "Peak requests per second", hint: "0 = derived from traffic" },
];

const inputClass =
  "planner-input w-full rounded-xl border px-3 py-2.5 text-sm text-ink " +
  "focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-red";

/** Human-in-the-loop gate shared by structured and AI-assisted intake. */
export function ReviewRequirements({
  requirements,
  warnings,
  patternLabel,
  tags = [],
  onConfirm,
  onBack,
}: {
  requirements: Record<string, unknown>;
  warnings: string[];
  patternLabel: string;
  tags?: string[];
  onConfirm: (requirements: Record<string, unknown>) => void;
  onBack: () => void;
}) {
  const [draft, setDraft] = useState<Record<string, unknown>>({ ...requirements });

  return (
    <PlannerWizardShell
      activeStep="review"
      title="Check the numbers before we price it"
      description="Every assumption remains editable. Costs are computed only after you confirm this review."
      selectedLabel={patternLabel}
      tags={tags}
      onBack={onBack}
      primaryLabel="Show cost estimate"
      onPrimary={() => onConfirm(draft)}
    >
      {warnings.length > 0 && (
        <div className="mb-4 rounded-xl border border-warning-accent bg-warning-surface p-3">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-status-warning">
            <AlertTriangle className="h-4 w-4" />
            Assumptions to check
          </h3>
          <ul aria-label="Extraction warnings" className="mt-2 space-y-1.5">
            {warnings.map((warning) => (
              <li key={warning} className="text-xs leading-5 text-ink-2">
                {warning}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="planner-form-panel grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {NUMERIC_FIELDS.map((field) => (
          <label key={field.key} className="block text-sm">
            <span className="font-medium text-ink">{field.label}</span>
            {field.hint && <span className="block text-xs text-muted">{field.hint}</span>}
            <input
              type="number"
              className={`${inputClass} mt-1.5`}
              value={Number(draft[field.key] ?? 0)}
              onChange={(event) =>
                setDraft((value) => ({
                  ...value,
                  [field.key]: Number(event.target.value),
                }))
              }
            />
          </label>
        ))}
      </div>
    </PlannerWizardShell>
  );
}

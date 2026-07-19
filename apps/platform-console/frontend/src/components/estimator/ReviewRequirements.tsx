import { useState } from "react";
import { Badge, Card, SectionTitle } from "../ui";

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
  "w-full rounded-lg border border-hairline bg-page px-3 py-2 text-sm text-ink " +
  "focus:outline-none focus-visible:ring-2 focus-visible:ring-series-1";

/** The human-in-the-loop gate: whether the numbers came from the form or the
 * AI extraction, a person confirms (and can edit) every value before the
 * engine prices anything. */
export function ReviewRequirements({
  requirements,
  warnings,
  patternLabel,
  onConfirm,
  onBack,
}: {
  requirements: Record<string, unknown>;
  warnings: string[];
  patternLabel: string;
  onConfirm: (requirements: Record<string, unknown>) => void;
  onBack: () => void;
}) {
  const [draft, setDraft] = useState<Record<string, unknown>>({ ...requirements });

  return (
    <Card>
      <SectionTitle
        title="Check the numbers before we price it"
        subtitle="The estimate is only as good as these inputs — every value below can be corrected."
      />
      <p className="mb-3 text-sm text-ink-2">
        Solution: <span className="font-semibold text-ink">{patternLabel}</span>
      </p>
      {warnings.length > 0 && (
        <ul aria-label="Extraction warnings" className="mb-4 space-y-1.5">
          {warnings.map((warning) => (
            <li key={warning} className="flex items-start gap-1.5 text-xs text-ink-2">
              <Badge tone="warning">check</Badge>
              <span>{warning}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        {NUMERIC_FIELDS.map((field) => (
          <label key={field.key} className="block text-sm">
            <span className="font-medium text-ink">{field.label}</span>
            {field.hint && <span className="block text-xs text-muted">{field.hint}</span>}
            <input
              type="number"
              className={`${inputClass} mt-1`}
              value={Number(draft[field.key] ?? 0)}
              onChange={(event) =>
                setDraft((d) => ({ ...d, [field.key]: Number(event.target.value) }))
              }
            />
          </label>
        ))}
      </div>
      <div className="mt-5 flex items-center justify-between">
        <button
          type="button"
          onClick={onBack}
          className="rounded-lg border border-hairline px-3 py-1.5 text-xs text-ink-2"
        >
          Back to the questions
        </button>
        <button
          type="button"
          onClick={() => onConfirm(draft)}
          className="rounded-lg bg-series-1 px-4 py-1.5 text-xs font-semibold text-page"
        >
          These numbers are right — show the costs
        </button>
      </div>
    </Card>
  );
}

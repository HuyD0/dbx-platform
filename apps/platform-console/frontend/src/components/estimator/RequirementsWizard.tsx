import { FileUp, Sparkles } from "lucide-react";
import { useState } from "react";
import type { EstimatorPattern } from "../../lib/types";
import { Badge, Card, SectionTitle } from "../ui";

export interface WizardDraft {
  pattern: string;
  monthly_requests: number;
  monthly_active_users: number;
  needs_memory: boolean | null;
  corpus_gb: number;
  corpus_growth_pct_monthly: number;
  region: string;
  currency: string;
}

const EMPTY: WizardDraft = {
  pattern: "",
  monthly_requests: 5000,
  monthly_active_users: 0,
  needs_memory: null,
  corpus_gb: 0,
  corpus_growth_pct_monthly: 2,
  region: "eastus",
  currency: "USD",
};

const TRAFFIC_PRESETS = [
  { label: "A small team trying it out", value: 2000 },
  { label: "A department using it daily", value: 20000 },
  { label: "The whole company", value: 100000 },
  { label: "Customer-facing", value: 500000 },
];

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block text-sm">
      <span className="font-medium text-ink">{label}</span>
      {hint && <span className="mt-0.5 block text-xs text-muted">{hint}</span>}
      <div className="mt-1.5">{children}</div>
    </label>
  );
}

/* Button groups must NOT sit inside a <label>: a wrapping label would become
 * every nested button's accessible name. */
function Group({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div role="group" aria-label={label} className="text-sm">
      <p className="font-medium text-ink">{label}</p>
      {hint && <p className="mt-0.5 text-xs text-muted">{hint}</p>}
      <div className="mt-1.5">{children}</div>
    </div>
  );
}

const inputClass =
  "w-full rounded-lg border border-hairline bg-page px-3 py-2 text-sm text-ink " +
  "focus:outline-none focus-visible:ring-2 focus-visible:ring-series-1";

/** Plain-English, progressive-disclosure intake. No AI needed on this path:
 * structured answers map straight onto the engine's requirements schema. The
 * optional free-text box hands off to the operator-gated extraction endpoint
 * and lands on the same review screen. */
export function RequirementsWizard({
  patterns,
  onComplete,
  onExtract,
  onUpload,
  extracting = false,
  extractError,
}: {
  patterns: EstimatorPattern[];
  onComplete: (draft: WizardDraft) => void;
  onExtract?: (text: string) => void;
  onUpload?: (file: File) => void;
  extracting?: boolean;
  extractError?: string;
}) {
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<WizardDraft>(EMPTY);
  const [freeText, setFreeText] = useState("");

  const selected = patterns.find((p) => p.pattern === draft.pattern);
  const needsKnowledge = Boolean(selected?.defaults.needs_knowledge_base);
  const steps = ["What should it do?", "Who will use it?"]
    .concat(needsKnowledge ? ["What should it know?"] : [])
    .concat(["Where should it run?"]);
  const lastStep = steps.length - 1;
  const update = (patch: Partial<WizardDraft>) => setDraft((d) => ({ ...d, ...patch }));

  return (
    <Card>
      <SectionTitle
        title={steps[step]}
        subtitle={`Step ${step + 1} of ${steps.length} — every answer can be edited on the review screen`}
      />
      <ol aria-label="Wizard progress" className="mb-4 flex flex-wrap gap-1.5">
        {steps.map((name, index) => (
          <li key={name}>
            <Badge tone={index < step ? "good" : index === step ? "info" : "neutral"}>
              {name}
            </Badge>
          </li>
        ))}
      </ol>

      {step === 0 && (
        <div className="space-y-4">
          <div role="radiogroup" aria-label="Solution pattern" className="grid gap-2 sm:grid-cols-2">
            {patterns.map((pattern) => (
              <button
                key={pattern.pattern}
                type="button"
                role="radio"
                aria-checked={draft.pattern === pattern.pattern}
                onClick={() => update({ pattern: pattern.pattern })}
                className={`rounded-xl border p-3 text-left transition-colors ${
                  draft.pattern === pattern.pattern
                    ? "border-series-1 bg-series-1/10"
                    : "border-hairline hover:border-series-1/50"
                }`}
              >
                <span className="block text-sm font-semibold text-ink">{pattern.label}</span>
                <span className="mt-1 block text-xs text-muted">{pattern.description}</span>
                <span className="mt-1.5 block text-xs italic text-ink-2">
                  “{pattern.example_prompt}”
                </span>
              </button>
            ))}
          </div>
          {onExtract && (
            <div className="rounded-xl border border-dashed border-hairline p-3">
              <Field
                label="Or describe it in your own words"
                hint="An AI model drafts the answers for you; you review and edit everything before any cost is computed."
              >
                <textarea
                  className={`${inputClass} min-h-20`}
                  value={freeText}
                  onChange={(event) => setFreeText(event.target.value)}
                  placeholder="e.g. Around 200 support agents should be able to ask questions about our policy documents…"
                />
              </Field>
              <button
                type="button"
                disabled={!freeText.trim() || extracting}
                onClick={() => onExtract(freeText)}
                className="mt-2 inline-flex items-center gap-1.5 rounded-lg bg-series-1 px-3 py-1.5 text-xs font-semibold text-page disabled:opacity-50"
              >
                <Sparkles className="h-3.5 w-3.5" />
                {extracting ? "Reading your description…" : "Draft the answers for me"}
              </button>
              {onUpload && (
                <div className="mt-3 border-t border-dashed border-hairline pt-3">
                  <label
                    htmlFor="estimator-document"
                    className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-hairline px-3 py-1.5 text-xs font-medium text-ink-2 hover:text-ink"
                  >
                    <FileUp className="h-3.5 w-3.5" />
                    Or upload a project document (PDF, Markdown or text, up to 10 MB)
                  </label>
                  <input
                    id="estimator-document"
                    type="file"
                    accept=".pdf,.md,.markdown,.txt"
                    className="sr-only"
                    disabled={extracting}
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      if (file) onUpload(file);
                      event.target.value = "";
                    }}
                  />
                  <p className="mt-1 text-xs text-muted">
                    Diagrams and images are not supported yet — describe those in
                    the text box instead.
                  </p>
                </div>
              )}
              {extractError && (
                <p role="alert" className="mt-2 text-xs text-danger">
                  {extractError}
                </p>
              )}
            </div>
          )}
        </div>
      )}

      {step === 1 && (
        <div className="space-y-4">
          <Group label="How often will it be used?" hint="Pick the closest scale — you can fine-tune the exact number next.">
            <div className="grid gap-2 sm:grid-cols-2">
              {TRAFFIC_PRESETS.map((preset) => (
                <button
                  key={preset.value}
                  type="button"
                  onClick={() => update({ monthly_requests: preset.value })}
                  className={`rounded-lg border px-3 py-2 text-left text-xs ${
                    draft.monthly_requests === preset.value
                      ? "border-series-1 bg-series-1/10 text-ink"
                      : "border-hairline text-ink-2 hover:border-series-1/50"
                  }`}
                >
                  <span className="font-medium">{preset.label}</span>
                  <span className="block text-muted">
                    ~{preset.value.toLocaleString("en-US")} requests / month
                  </span>
                </button>
              ))}
            </div>
          </Group>
          <Field label="Requests per month" hint="Total questions or tasks in a typical month.">
            <input
              type="number"
              min={1}
              className={inputClass}
              value={draft.monthly_requests}
              onChange={(event) => update({ monthly_requests: Number(event.target.value) })}
            />
          </Field>
          <Field label="People using it each month" hint="Optional — helps size per-user features.">
            <input
              type="number"
              min={0}
              className={inputClass}
              value={draft.monthly_active_users}
              onChange={(event) => update({ monthly_active_users: Number(event.target.value) })}
            />
          </Field>
          <Group
            label="Should it remember each user between sessions?"
            hint="Adds a small always-on database so the system can pick up where each person left off."
          >
            <div className="flex gap-2">
              {[
                { label: `Pattern default${selected?.defaults.needs_memory ? " (yes)" : " (no)"}`, value: null },
                { label: "Yes", value: true },
                { label: "No", value: false },
              ].map((option) => (
                <button
                  key={option.label}
                  type="button"
                  onClick={() => update({ needs_memory: option.value })}
                  className={`rounded-lg border px-3 py-1.5 text-xs ${
                    draft.needs_memory === option.value
                      ? "border-series-1 bg-series-1/10 text-ink"
                      : "border-hairline text-ink-2"
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </Group>
        </div>
      )}

      {needsKnowledge && step === 2 && (
        <div className="space-y-4">
          <Field
            label="How much material should it know? (GB)"
            hint="Rough size of the documents it must answer from. Leave 0 to use a sensible default."
          >
            <input
              type="number"
              min={0}
              className={inputClass}
              value={draft.corpus_gb}
              onChange={(event) => update({ corpus_gb: Number(event.target.value) })}
            />
          </Field>
          <Field label="How fast does that material grow? (% per month)">
            <input
              type="number"
              min={0}
              max={100}
              className={inputClass}
              value={draft.corpus_growth_pct_monthly}
              onChange={(event) =>
                update({ corpus_growth_pct_monthly: Number(event.target.value) })
              }
            />
          </Field>
        </div>
      )}

      {step === lastStep && step > 0 && (
        <div className="space-y-4">
          <Field label="Cloud region">
            <input
              className={inputClass}
              value={draft.region}
              onChange={(event) => update({ region: event.target.value })}
            />
          </Field>
          <Field label="Currency">
            <input
              className={inputClass}
              value={draft.currency}
              onChange={(event) => update({ currency: event.target.value.toUpperCase() })}
            />
          </Field>
        </div>
      )}

      <div className="mt-5 flex items-center justify-between">
        <button
          type="button"
          onClick={() => setStep((s) => Math.max(0, s - 1))}
          disabled={step === 0}
          className="rounded-lg border border-hairline px-3 py-1.5 text-xs text-ink-2 disabled:opacity-40"
        >
          Back
        </button>
        {step < lastStep ? (
          <button
            type="button"
            disabled={step === 0 && !draft.pattern}
            onClick={() => setStep((s) => s + 1)}
            className="rounded-lg bg-series-1 px-4 py-1.5 text-xs font-semibold text-page disabled:opacity-50"
          >
            Next
          </button>
        ) : (
          <button
            type="button"
            onClick={() => onComplete(draft)}
            className="rounded-lg bg-series-1 px-4 py-1.5 text-xs font-semibold text-page"
          >
            Review my answers
          </button>
        )}
      </div>
    </Card>
  );
}

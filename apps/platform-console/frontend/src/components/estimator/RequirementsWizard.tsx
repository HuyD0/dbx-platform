import {
  AlignLeft,
  Bot,
  Check,
  ChevronDown,
  FileUp,
  MessageSquareText,
  Sparkles,
  TableProperties,
} from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import type { EstimatorPattern } from "../../lib/types";
import { PlannerWizardShell, type PlannerStep } from "./PlannerWizardShell";

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

export type DraftingAccess = "checking" | "available" | "unavailable";

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

const STEP_COPY: Record<PlannerStep, { title: string; description: string }> = {
  solution: {
    title: "Choose a solution",
    description:
      "Pick the closest starting point, or let AI draft requirements from a description or project document.",
  },
  usage: {
    title: "Size expected usage",
    description:
      "Choose the closest operating scale, then adjust the numbers that drive the estimate.",
  },
  knowledge: {
    title: "Describe the knowledge base",
    description:
      "Estimate how much source material the solution needs and how quickly it will grow.",
  },
  region: {
    title: "Choose where it runs",
    description:
      "Set the pricing region and reporting currency. You can correct both before costs are computed.",
  },
  review: {
    title: "Review",
    description: "",
  },
};

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
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
  children: ReactNode;
}) {
  return (
    <div role="group" aria-label={label} className="text-sm">
      <p className="font-medium text-ink">{label}</p>
      {hint && <p className="mt-0.5 text-xs text-muted">{hint}</p>}
      <div className="mt-2">{children}</div>
    </div>
  );
}

const inputClass =
  "planner-input w-full rounded-xl border px-3 py-2.5 text-sm text-ink " +
  "focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-red";

function patternIcon(pattern: string, index: number) {
  if (pattern.includes("chat")) return MessageSquareText;
  if (pattern.includes("extract")) return TableProperties;
  if (pattern.includes("summar")) return AlignLeft;
  return index === 3 ? Bot : Sparkles;
}

export function estimatorPatternTags(pattern?: EstimatorPattern): string[] {
  if (!pattern) return [];
  const tags: string[] = [];
  if (pattern.defaults.needs_knowledge_base) tags.push("Knowledge base");
  if (pattern.defaults.needs_memory) tags.push("Conversation memory");
  if (pattern.pattern.includes("chat")) tags.push("Natural language Q&A");
  else if (pattern.pattern.includes("extract")) tags.push("Structured output");
  else if (pattern.pattern.includes("summar")) tags.push("Content summarization");
  else tags.push("AI workflow");
  return tags;
}

/** Plain-English, progressive-disclosure intake. Structured answers map
 * directly to the deterministic engine. AI extraction remains optional,
 * operator-gated, and always lands on the same human review screen. */
export function RequirementsWizard({
  patterns,
  onComplete,
  onExtract,
  onUpload,
  draftingAccess = "unavailable",
  extracting = false,
  extractError,
}: {
  patterns: EstimatorPattern[];
  onComplete: (draft: WizardDraft) => void;
  onExtract?: (text: string) => void;
  onUpload?: (file: File) => void;
  draftingAccess?: DraftingAccess;
  extracting?: boolean;
  extractError?: string;
}) {
  const [stepIndex, setStepIndex] = useState(0);
  const [draft, setDraft] = useState<WizardDraft>(EMPTY);
  const [freeText, setFreeText] = useState("");
  const [showDetails, setShowDetails] = useState(false);

  const selected = patterns.find((pattern) => pattern.pattern === draft.pattern);
  const needsKnowledge = Boolean(selected?.defaults.needs_knowledge_base);
  const flow = useMemo<PlannerStep[]>(
    () => ["solution", "usage", ...(needsKnowledge ? (["knowledge"] as const) : []), "region"],
    [needsKnowledge],
  );
  const boundedStepIndex = Math.min(stepIndex, flow.length - 1);
  const activeStep = flow[boundedStepIndex];
  const update = (patch: Partial<WizardDraft>) => setDraft((value) => ({ ...value, ...patch }));
  const tags = estimatorPatternTags(selected);
  const canDraft = draftingAccess === "available";
  const aiDisabled = !canDraft || extracting;
  const accessLabel =
    draftingAccess === "checking"
      ? "Checking AI access…"
      : canDraft
        ? "AI drafting available"
        : "Requires operator access";

  const goBack = () => setStepIndex((value) => Math.max(0, value - 1));
  const goForward = () => {
    if (boundedStepIndex < flow.length - 1) {
      setStepIndex((value) => value + 1);
    } else {
      onComplete(draft);
    }
  };

  const primaryLabel =
    activeStep === "solution"
      ? "Continue to usage"
      : activeStep === "usage"
        ? needsKnowledge
          ? "Continue to knowledge"
          : "Continue to region"
        : activeStep === "knowledge"
          ? "Continue to region"
          : "Review requirements";

  return (
    <PlannerWizardShell
      activeStep={activeStep}
      skippedSteps={selected && !needsKnowledge ? ["knowledge"] : []}
      title={STEP_COPY[activeStep].title}
      description={STEP_COPY[activeStep].description}
      selectedLabel={selected?.label}
      tags={tags}
      onBack={goBack}
      backDisabled={boundedStepIndex === 0}
      primaryLabel={primaryLabel}
      onPrimary={goForward}
      primaryDisabled={activeStep === "solution" && !draft.pattern}
    >
      {activeStep === "solution" && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(20rem,2fr)]">
          <div>
            <div
              role="radiogroup"
              aria-label="Solution pattern"
              className="space-y-2"
            >
              {patterns.map((pattern, index) => {
                const Icon = patternIcon(pattern.pattern, index);
                const checked = draft.pattern === pattern.pattern;
                return (
                  <button
                    key={pattern.pattern}
                    type="button"
                    role="radio"
                    aria-checked={checked}
                    onClick={() => update({ pattern: pattern.pattern })}
                    className={`planner-solution-row ${checked ? "is-selected" : ""}`}
                  >
                    <span className="planner-solution-icon">
                      <Icon className="h-5 w-5" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-semibold leading-5 text-ink">
                        {pattern.label}
                      </span>
                      <span className="mt-0.5 block text-xs leading-5 text-muted">
                        {pattern.description}
                      </span>
                      {showDetails && (
                        <span className="mt-1 block text-xs italic leading-5 text-ink-2">
                          “{pattern.example_prompt}”
                        </span>
                      )}
                    </span>
                    <span className="planner-radio" aria-hidden="true">
                      {checked && <span />}
                    </span>
                  </button>
                );
              })}
            </div>
            <button
              type="button"
              onClick={() => setShowDetails((value) => !value)}
              aria-expanded={showDetails}
              className="mt-2 inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-medium text-ink-2 hover:bg-hairline hover:text-ink"
            >
              <ChevronDown
                className={`h-3.5 w-3.5 transition-transform ${showDetails ? "rotate-180" : ""}`}
              />
              {showDetails ? "Hide examples" : "Show examples"}
            </button>
          </div>

          <div className="planner-intake-panel" aria-labelledby="project-intake-title">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <h3 id="project-intake-title" className="text-base font-semibold text-ink">
                Start from your project
              </h3>
              <span className={`planner-access-badge ${canDraft ? "is-available" : ""}`}>
                <Sparkles className="h-3 w-3" />
                {accessLabel}
              </span>
            </div>
            <Field
              label="Describe it in your own words"
              hint={
                canDraft
                  ? "AI will draft structured requirements for you to review."
                  : "The manual solution choices remain available to everyone."
              }
            >
              <textarea
                className={`${inputClass} min-h-28 resize-y`}
                value={freeText}
                disabled={!canDraft}
                onChange={(event) => setFreeText(event.target.value)}
                placeholder="Around 200 support agents need answers from policy documents…"
              />
            </Field>
            <button
              type="button"
              disabled={!freeText.trim() || aiDisabled || !onExtract}
              onClick={() => onExtract?.(freeText)}
              className="planner-primary-button mt-3 w-full justify-center"
            >
              <Sparkles className="h-4 w-4" />
              {extracting ? "Reading your description…" : "Draft requirements"}
            </button>

            <div className="my-3 flex items-center gap-3 text-[11px] text-muted">
              <span className="h-px flex-1 bg-grid" />
              or
              <span className="h-px flex-1 bg-grid" />
            </div>

            <label
              htmlFor="estimator-document"
              className={`planner-upload-zone ${aiDisabled || !onUpload ? "is-disabled" : ""}`}
            >
              <FileUp className="h-5 w-5" />
              <span>
                <span className="block text-sm font-semibold text-ink">
                  Upload a project document
                </span>
                <span className="mt-0.5 block text-xs text-muted">
                  PDF, Markdown or text · up to 10 MB
                </span>
              </span>
            </label>
            <input
              id="estimator-document"
              type="file"
              accept=".pdf,.md,.markdown,.txt"
              className="sr-only"
              disabled={aiDisabled || !onUpload}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) onUpload?.(file);
                event.target.value = "";
              }}
            />
            <p className="mt-2 flex items-center justify-center gap-1.5 text-center text-[11px] text-muted">
              <Check className="h-3.5 w-3.5" />
              You’ll review every assumption before pricing.
            </p>
            {extractError && (
              <p role="alert" className="mt-2 text-xs text-status-critical">
                {extractError}
              </p>
            )}
          </div>
        </div>
      )}

      {activeStep === "usage" && (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(18rem,0.9fr)]">
          <Group
            label="How often will it be used?"
            hint="Pick the closest scale — you can fine-tune the exact number alongside it."
          >
            <div className="grid gap-2 sm:grid-cols-2">
              {TRAFFIC_PRESETS.map((preset) => (
                <button
                  key={preset.value}
                  type="button"
                  onClick={() => update({ monthly_requests: preset.value })}
                  className={`planner-preset-button ${
                    draft.monthly_requests === preset.value ? "is-selected" : ""
                  }`}
                >
                  <span className="font-medium text-ink">{preset.label}</span>
                  <span className="mt-0.5 block text-muted">
                    ~{preset.value.toLocaleString("en-US")} requests / month
                  </span>
                </button>
              ))}
            </div>
          </Group>
          <div className="planner-form-panel space-y-4">
            <Field
              label="Requests per month"
              hint="Total questions or tasks in a typical month."
            >
              <input
                type="number"
                min={1}
                className={inputClass}
                value={draft.monthly_requests}
                onChange={(event) => update({ monthly_requests: Number(event.target.value) })}
              />
            </Field>
            <Field
              label="People using it each month"
              hint="Optional — helps size per-user features."
            >
              <input
                type="number"
                min={0}
                className={inputClass}
                value={draft.monthly_active_users}
                onChange={(event) =>
                  update({ monthly_active_users: Number(event.target.value) })
                }
              />
            </Field>
            <Group
              label="Remember users between sessions?"
              hint="Adds a small always-on database for continuity."
            >
              <div className="flex flex-wrap gap-2">
                {[
                  {
                    label: `Pattern default${selected?.defaults.needs_memory ? " (yes)" : " (no)"}`,
                    value: null,
                  },
                  { label: "Yes", value: true },
                  { label: "No", value: false },
                ].map((option) => (
                  <button
                    key={option.label}
                    type="button"
                    onClick={() => update({ needs_memory: option.value })}
                    className={`planner-option-button ${
                      draft.needs_memory === option.value ? "is-selected" : ""
                    }`}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </Group>
          </div>
        </div>
      )}

      {activeStep === "knowledge" && (
        <div className="planner-form-panel mx-auto grid max-w-3xl gap-4 sm:grid-cols-2">
          <Field
            label="Knowledge base size (GB)"
            hint="Rough size of the documents it must answer from. Leave 0 for the pattern default."
          >
            <input
              type="number"
              min={0}
              className={inputClass}
              value={draft.corpus_gb}
              onChange={(event) => update({ corpus_gb: Number(event.target.value) })}
            />
          </Field>
          <Field
            label="Monthly growth (%)"
            hint="How quickly the source material is expected to grow."
          >
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

      {activeStep === "region" && (
        <div className="planner-form-panel mx-auto grid max-w-3xl gap-4 sm:grid-cols-2">
          <Field label="Cloud region" hint="Used to select applicable infrastructure prices.">
            <input
              className={inputClass}
              value={draft.region}
              onChange={(event) => update({ region: event.target.value })}
            />
          </Field>
          <Field label="Currency" hint="The estimate will keep cost bases separate.">
            <input
              className={inputClass}
              value={draft.currency}
              onChange={(event) => update({ currency: event.target.value.toUpperCase() })}
            />
          </Field>
        </div>
      )}
    </PlannerWizardShell>
  );
}

import { Check, ChevronRight } from "lucide-react";
import type { ReactNode } from "react";

export type PlannerStep = "solution" | "usage" | "knowledge" | "region" | "review";

const STEPS: { id: PlannerStep; label: string }[] = [
  { id: "solution", label: "Solution" },
  { id: "usage", label: "Usage" },
  { id: "knowledge", label: "Knowledge" },
  { id: "region", label: "Region" },
  { id: "review", label: "Review" },
];

export function PlannerTag({ children }: { children: ReactNode }) {
  return <span className="planner-tag">{children}</span>;
}

/** Shared frame for every human-confirmed estimator stage. The glass surfaces
 * provide hierarchy; inputs remain opaque and the action dock stays clear of
 * global launchers. */
export function PlannerWizardShell({
  activeStep,
  skippedSteps = [],
  title,
  description,
  selectedLabel,
  tags = [],
  children,
  onBack,
  backDisabled = false,
  primaryLabel,
  onPrimary,
  primaryDisabled = false,
}: {
  activeStep: PlannerStep;
  skippedSteps?: PlannerStep[];
  title: string;
  description: string;
  selectedLabel?: string;
  tags?: string[];
  children: ReactNode;
  onBack: () => void;
  backDisabled?: boolean;
  primaryLabel: string;
  onPrimary: () => void;
  primaryDisabled?: boolean;
}) {
  const activeIndex = STEPS.findIndex((step) => step.id === activeStep);

  return (
    <section className="planner-ambient space-y-3" aria-labelledby="planner-stage-title">
      <nav className="planner-glass planner-progress" aria-label="Cost Planner progress">
        <ol className="grid grid-cols-5">
          {STEPS.map((step, index) => {
            const skipped = skippedSteps.includes(step.id);
            const complete = index < activeIndex || skipped;
            const active = step.id === activeStep;
            return (
              <li
                key={step.id}
                aria-current={active ? "step" : undefined}
                className={`planner-progress-step ${active ? "is-active" : ""} ${
                  complete ? "is-complete" : ""
                } ${skipped ? "is-skipped" : ""}`}
              >
                <span className="planner-progress-marker" aria-hidden="true">
                  {complete && !active ? <Check className="h-3 w-3" /> : index + 1}
                </span>
                <span className="truncate">{step.label}</span>
                {skipped && <span className="sr-only"> skipped</span>}
              </li>
            );
          })}
        </ol>
      </nav>

      <div className="planner-glass planner-workspace">
        <header className="mb-5">
          <h2 id="planner-stage-title" className="text-lg font-semibold tracking-tight text-ink">
            {title}
          </h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-ink-2">{description}</p>
        </header>
        {children}
      </div>

      {selectedLabel && (
        <div className="planner-glass planner-selection-summary" aria-label="Current selection">
          <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-primary-red text-white">
            <Check className="h-4 w-4" />
          </span>
          <span className="text-sm text-ink-2">
            Selected: <strong className="font-semibold text-ink">{selectedLabel}</strong>
          </span>
          {tags.length > 0 && (
            <div className="flex flex-wrap gap-1.5 sm:ml-2">
              {tags.map((tag) => (
                <PlannerTag key={tag}>{tag}</PlannerTag>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="planner-glass planner-action-dock">
        <button
          type="button"
          onClick={onBack}
          disabled={backDisabled}
          className="planner-secondary-button"
        >
          Back
        </button>
        <button
          type="button"
          onClick={onPrimary}
          disabled={primaryDisabled}
          className="planner-primary-button"
        >
          {primaryLabel}
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </section>
  );
}

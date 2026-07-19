import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { BlueprintPanel } from "../components/estimator/BlueprintPanel";
import {
  EstimateLibrary,
  SaveEstimateButton,
} from "../components/estimator/EstimateLibrary";
import { EvaluationTaxPanel } from "../components/estimator/EvaluationTaxPanel";
import { LineItemTable } from "../components/estimator/LineItemTable";
import { SimilarEstimates } from "../components/estimator/SimilarEstimates";
import { PricingFreshness } from "../components/estimator/PricingFreshness";
import { RequirementsWizard, type WizardDraft } from "../components/estimator/RequirementsWizard";
import { ReviewRequirements } from "../components/estimator/ReviewRequirements";
import { RigorSlider } from "../components/estimator/RigorSlider";
import { ScenarioToggle } from "../components/estimator/ScenarioToggle";
import { TcoMatrix } from "../components/estimator/TcoMatrix";
import { EmptyState, ErrorState, PageHeader, Skeleton } from "../components/ui";
import { apiGet, apiPost } from "../lib/api";
import {
  ApiError,
  type Envelope,
  type EstimateMatrix,
  type EstimatorPattern,
  type ExtractResponse,
  type SavedEstimateSummary,
} from "../lib/types";

type Phase = "wizard" | "review" | "results";

/** AI Cost Planner: plain-English wizard → human review → deterministic
 * 3-tier TCO matrix. All math happens server-side in the tested engine; the
 * one AI step (free-text extraction) only pre-fills the review screen. */
export function CostPlanner() {
  const [phase, setPhase] = useState<Phase>("wizard");
  const [requirements, setRequirements] = useState<Record<string, unknown>>({});
  const [warnings, setWarnings] = useState<string[]>([]);
  const [confirmed, setConfirmed] = useState<Record<string, unknown> | null>(null);
  const [rigorPct, setRigorPct] = useState(10);
  const [scenario, setScenario] = useState("databricks");
  const [tier, setTier] = useState("production");

  const patterns = useQuery({
    queryKey: ["estimator", "patterns"],
    queryFn: () => apiGet<Envelope<EstimatorPattern[]>>("/api/estimator/patterns"),
    staleTime: 60 * 60_000,
  });

  const extract = useMutation({
    mutationFn: (text: string) =>
      apiPost<ExtractResponse>("/api/estimator/extract", { text }),
    onSuccess: (response) => {
      setRequirements(response.requirements);
      setWarnings(response.warnings);
      setPhase("review");
    },
  });

  const estimate = useQuery({
    queryKey: ["estimator", "estimate", confirmed, rigorPct],
    queryFn: () =>
      apiPost<Envelope<EstimateMatrix>>("/api/estimator/estimate", {
        requirements: confirmed,
        rigor_pct: rigorPct,
      }),
    enabled: phase === "results" && confirmed !== null,
    placeholderData: (previous) => previous,
    staleTime: 5 * 60_000,
  });

  const patternLabel = useMemo(() => {
    const key = String(requirements.pattern ?? "");
    return patterns.data?.data.find((p) => p.pattern === key)?.label ?? key;
  }, [patterns.data, requirements.pattern]);

  const completeWizard = (draft: WizardDraft) => {
    setRequirements({ ...draft });
    setWarnings([]);
    setPhase("review");
  };

  const confirmReview = (edited: Record<string, unknown>) => {
    setConfirmed(edited);
    setPhase("results");
  };

  const reuseSaved = (estimate: SavedEstimateSummary) => {
    try {
      setRequirements(JSON.parse(estimate.requirements_json) as Record<string, unknown>);
    } catch {
      return; // a malformed stored document must not crash the wizard
    }
    setWarnings([`Started from the saved estimate “${estimate.title}”.`]);
    setPhase("review");
  };

  const matrix = estimate.data?.data;
  const activeTier = matrix?.tiers[tier];
  const activeEstimate = activeTier?.scenarios[scenario];

  return (
    <div className="space-y-4">
      <PageHeader
        eyebrow="Cost"
        title="AI Cost Planner"
        description="Describe an AI solution in plain language and get a defensible monthly cost across three operating tiers — with the cost of checking the AI's work shown separately, never hidden."
        actions={
          phase === "results" ? (
            <button
              type="button"
              onClick={() => {
                setPhase("wizard");
                setConfirmed(null);
              }}
              className="rounded-lg border border-hairline px-3 py-1.5 text-xs text-ink-2"
            >
              Start a new estimate
            </button>
          ) : undefined
        }
      />

      {phase === "wizard" &&
        (patterns.isPending ? (
          <Skeleton rows={6} />
        ) : patterns.isError ? (
          <ErrorState error={patterns.error} />
        ) : (
          <RequirementsWizard
            patterns={patterns.data.data}
            onComplete={completeWizard}
            onExtract={(text) => extract.mutate(text)}
            extracting={extract.isPending}
            extractError={
              extract.isError
                ? extract.error instanceof ApiError && extract.error.status === 403
                  ? "Drafting answers with AI needs operator access — the form works for everyone."
                  : (extract.error as Error).message
                : undefined
            }
          />
        ))}

      {phase === "review" && (
        <>
          <ReviewRequirements
            requirements={requirements}
            warnings={warnings}
            patternLabel={patternLabel}
            onConfirm={confirmReview}
            onBack={() => setPhase("wizard")}
          />
          <SimilarEstimates
            pattern={String(requirements.pattern ?? "")}
            monthlyRequests={Number(requirements.monthly_requests ?? 0)}
            onReuse={reuseSaved}
          />
        </>
      )}

      {phase === "results" && (
        <>
          {estimate.isPending && <Skeleton rows={8} />}
          {estimate.isError && <ErrorState error={estimate.error} />}
          {matrix && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <ScenarioToggle scenario={scenario} onChange={setScenario} />
                <PricingFreshness snapshotDate={matrix.snapshot_date} />
              </div>
              {confirmed && (
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <SaveEstimateButton requirements={confirmed} rigorPct={rigorPct} />
                  <SimilarEstimates
                    pattern={String(confirmed.pattern ?? "")}
                    monthlyRequests={Number(confirmed.monthly_requests ?? 0)}
                    requirementsHash={matrix.requirements_hash}
                    onReuse={reuseSaved}
                  />
                </div>
              )}
              <TcoMatrix
                matrix={matrix}
                scenario={scenario}
                rigorPct={rigorPct}
                selectedTier={tier}
                onSelectTier={setTier}
              />
              {activeTier && (
                <RigorSlider
                  tier={activeTier}
                  scenario={scenario}
                  rigorPct={rigorPct}
                  onChange={setRigorPct}
                />
              )}
              {activeEstimate && (
                <div className="grid gap-4 lg:grid-cols-2">
                  <EvaluationTaxPanel estimate={activeEstimate} rigorPct={rigorPct} />
                  <BlueprintPanel blueprint={matrix.blueprint} />
                </div>
              )}
              {activeEstimate && <LineItemTable estimate={activeEstimate} />}
              <EstimateLibrary onReuse={reuseSaved} />
            </div>
          )}
          {!matrix && !estimate.isPending && !estimate.isError && (
            <EmptyState message="No estimate yet — complete the wizard to compute one." />
          )}
        </>
      )}
    </div>
  );
}

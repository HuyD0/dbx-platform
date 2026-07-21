import { useMutation, useQuery } from "@tanstack/react-query";
import { Bot } from "lucide-react";
import { useMemo, useState } from "react";
import { BlueprintPanel } from "../components/estimator/BlueprintPanel";
import { DeploymentsPanel } from "../components/estimator/DeploymentsPanel";
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
import { adjustedTotals } from "../components/estimator/curve";
import { EmptyState, ErrorState, PageHeader, Skeleton } from "../components/ui";
import { apiGet, apiPost, apiUpload } from "../lib/api";
import { useAssistantPanel } from "../lib/assistant-panel";
import { useChat } from "../lib/chat";
import { usd } from "../lib/format";
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

  const uploadDocument = useMutation({
    mutationFn: (file: File) =>
      apiUpload<ExtractResponse & { filename: string }>(
        "/api/estimator/extract-document",
        file,
      ),
    onSuccess: (response) => {
      setRequirements(response.requirements);
      setWarnings([`Read from “${response.filename}”.`, ...response.warnings]);
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

  const openAssistant = useAssistantPanel();
  const { send: sendToAssistant, pending: assistantPending } = useChat();

  /** Pre-contextualize the read-only assistant with the tier + scenario the
   * user is currently evaluating, so it can explain the SKUs/cost drivers
   * without the user re-typing any of these parameters. When the selected tier
   * is the cheapest of the three, the seeded question leans into entry-level
   * SKU comparison, matching what the user is actually weighing. */
  const askAboutTier = () => {
    if (!matrix || !activeTier || !activeEstimate || assistantPending) return;
    const envTotals = adjustedTotals(activeEstimate, rigorPct);
    const monthly = (["dev", "uat", "prod"] as const).reduce(
      (sum, env) => sum + (envTotals[env]?.total ?? 0),
      0,
    );
    const grandByTier = Object.entries(matrix.tiers).map(([key, t]) => {
      const est = t.scenarios[scenario];
      const byEnv = est ? adjustedTotals(est, rigorPct) : {};
      const total = (["dev", "uat", "prod"] as const).reduce(
        (sum, env) => sum + (byEnv[env]?.total ?? 0),
        0,
      );
      return { key, total };
    });
    const cheapest = grandByTier.reduce(
      (min, row) => (row.total < min.total ? row : min),
      grandByTier[0],
    );
    const isEntryLevel = cheapest?.key === tier;

    const focus = {
      actionId: `estimate:${matrix.requirements_hash}:${tier}:${scenario}`,
      label: `${activeTier.label} tier · ${scenario}`,
    };
    openAssistant(focus);
    sendToAssistant(
      `I'm evaluating the "${activeTier.label}" tier on ${scenario} at ${rigorPct}% review ` +
        `coverage, roughly ${usd(monthly)} / month across all environments. ` +
        (isEntryLevel
          ? "This is the cheapest of the three tiers — compare the entry-level SKUs and explain " +
            "what I'd give up versus the next tier up. "
          : "Explain the main cost drivers and whether a cheaper tier would still fit. ") +
        "Cite pricing evidence and stay read-only.",
      focus,
    );
  };

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
            onUpload={(file) => uploadDocument.mutate(file)}
            extracting={extract.isPending || uploadDocument.isPending}
            extractError={(() => {
              const error = extract.isError
                ? extract.error
                : uploadDocument.isError
                  ? uploadDocument.error
                  : undefined;
              if (!error) return undefined;
              return error instanceof ApiError && error.status === 403
                ? "Drafting answers with AI needs operator access — the form works for everyone."
                : (error as Error).message;
            })()}
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
                <div className="flex flex-wrap items-center gap-3">
                  <button
                    type="button"
                    onClick={askAboutTier}
                    disabled={assistantPending}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-grid px-3 py-1.5 text-xs font-medium text-ink hover:bg-hairline disabled:opacity-50"
                  >
                    <Bot className="h-3.5 w-3.5 text-accent" />
                    Ask about the {activeTier?.label ?? "selected"} tier
                  </button>
                  <PricingFreshness snapshotDate={matrix.snapshot_date} />
                </div>
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
              <DeploymentsPanel />
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

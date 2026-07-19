import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe, toHaveNoViolations } from "jest-axe";
import { expect, test, vi } from "vitest";
import type {
  EstimateMatrix,
  EstimateTier,
  EstimatorPattern,
  TierScenarioEstimate,
} from "../../lib/types";
import { RequirementsWizard } from "./RequirementsWizard";
import { RigorSlider } from "./RigorSlider";
import { TcoMatrix } from "./TcoMatrix";
import { adjustedTotals, applyRigor } from "./curve";

expect.extend(toHaveNoViolations);

function estimateFixture(overrides: Partial<TierScenarioEstimate> = {}): TierScenarioEstimate {
  return {
    tier: "production",
    scenario: "azure",
    rigor_pct: 10,
    line_items: [],
    totals_by_env: { dev: 20, uat: 30, prod: 150 },
    run_cost_by_env: { dev: 15, uat: 20, prod: 100 },
    eval_tax_by_env: { dev: 5, uat: 10, prod: 50 },
    improvement_pipeline_by_env: { dev: 5, uat: 10, prod: 0 },
    missing_prices: [],
    rigor_curve: {
      pinned: false,
      by_env: {
        dev: { total_fixed: 20, total_slope_per_pct: 0, eval_fixed: 5, eval_slope_per_pct: 0 },
        uat: { total_fixed: 30, total_slope_per_pct: 0, eval_fixed: 10, eval_slope_per_pct: 0 },
        prod: {
          total_fixed: 110,
          total_slope_per_pct: 4,
          eval_fixed: 10,
          eval_slope_per_pct: 4,
        },
      },
    },
    ...overrides,
  };
}

function tierFixture(overrides: Partial<EstimateTier> = {}): EstimateTier {
  return {
    label: "Production standard",
    description: "Serve real users.",
    rigor_locked: false,
    rigor_locked_reason: "",
    default_rigor_pct: 10,
    scenarios: { azure: estimateFixture(), databricks: estimateFixture({ scenario: "databricks" }) },
    ...overrides,
  };
}

// --- curve math ---------------------------------------------------------------

test("applyRigor is exact affine math and clamps to 0-100", () => {
  const curve = { total_fixed: 110, total_slope_per_pct: 4, eval_fixed: 10, eval_slope_per_pct: 4 };
  expect(applyRigor(curve, 10)).toEqual({ total: 150, evalTax: 50 });
  expect(applyRigor(curve, 0)).toEqual({ total: 110, evalTax: 10 });
  expect(applyRigor(curve, 250).total).toBe(510); // clamped to 100
  expect(applyRigor(curve, -5).total).toBe(110);
});

test("adjustedTotals respects pinned tiers and derives run cost", () => {
  const unpinned = adjustedTotals(estimateFixture(), 50);
  expect(unpinned.prod.total).toBe(310);
  expect(unpinned.prod.evalTax).toBe(210);
  expect(unpinned.prod.runCost).toBe(100);

  const pinned = adjustedTotals(
    estimateFixture({ rigor_pct: 100, rigor_curve: { ...estimateFixture().rigor_curve, pinned: true } }),
    5,
  );
  expect(pinned.prod.total).toBe(510); // pinned at the tier's own rigor (100), not 5
});

// --- RigorSlider --------------------------------------------------------------

test("rigor slider recomputes checking cost client-side and stays accessible", async () => {
  const onChange = vi.fn();
  const { container } = render(
    <RigorSlider tier={tierFixture()} scenario="azure" rigorPct={10} onChange={onChange} />,
  );
  expect(screen.getByText("$50.00 / month")).toBeInTheDocument();
  const slider = screen.getByRole("slider");
  expect(slider).not.toBeDisabled();
  fireEvent.change(slider, { target: { value: "35" } });
  expect(onChange).toHaveBeenCalledWith(35);
  expect(await axe(container)).toHaveNoViolations();
});

test("locked tiers pin the slider and explain why", () => {
  const locked = tierFixture({
    rigor_locked: true,
    rigor_locked_reason: "Compliance requires reviewing every answer.",
    scenarios: {
      azure: estimateFixture({
        rigor_pct: 100,
        rigor_curve: { ...estimateFixture().rigor_curve, pinned: true },
      }),
    },
  });
  render(<RigorSlider tier={locked} scenario="azure" rigorPct={10} onChange={() => {}} />);
  expect(screen.getByRole("slider")).toBeDisabled();
  expect(screen.getByTestId("rigor-locked-reason")).toHaveTextContent("Compliance requires");
});

// --- TcoMatrix ----------------------------------------------------------------

test("TCO matrix shows run/checking split per environment and flags missing prices", async () => {
  const matrix: EstimateMatrix = {
    engine_version: "1",
    rate_card_version: "2026.07.1",
    snapshot_date: "2026-07-14",
    requirements: {},
    rigor_pct: 10,
    requirements_hash: "x".repeat(64),
    blueprint: [],
    tiers: {
      production: tierFixture(),
      fiduciary: tierFixture({
        label: "Fiduciary-grade compliance",
        rigor_locked: true,
        rigor_locked_reason: "Fixed at 100%.",
        scenarios: {
          azure: estimateFixture({
            rigor_pct: 100,
            missing_prices: ["state_store/prod: Remembering users"],
            rigor_curve: { ...estimateFixture().rigor_curve, pinned: true },
          }),
        },
      }),
    },
  };
  const { container } = render(
    <TcoMatrix
      matrix={matrix}
      scenario="azure"
      rigorPct={10}
      selectedTier="production"
      onSelectTier={() => {}}
    />,
  );
  const table = screen.getByTestId("tco-matrix");
  const productionRow = within(table).getByRole("row", { name: /Production standard/ });
  expect(productionRow).toHaveTextContent("run $100");
  expect(productionRow).toHaveTextContent("checking $50.00");
  expect(within(table).getByText(/1 price\(s\) unavailable/)).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

// --- RequirementsWizard -------------------------------------------------------

const patterns: EstimatorPattern[] = [
  {
    pattern: "doc_chat",
    label: "Chat with your documents",
    description: "Answers grounded in your files.",
    example_prompt: "What does our policy say?",
    defaults: { needs_knowledge_base: true, needs_memory: false },
  },
  {
    pattern: "summarize",
    label: "Summarize long content",
    description: "Short consistent summaries.",
    example_prompt: "Summarize this report.",
    defaults: { needs_knowledge_base: false, needs_memory: false },
  },
];

test("wizard walks pattern -> usage -> knowledge -> region and completes", async () => {
  const user = userEvent.setup();
  const onComplete = vi.fn();
  const { container } = render(
    <RequirementsWizard patterns={patterns} onComplete={onComplete} />,
  );
  expect(await axe(container)).toHaveNoViolations();

  // Step 1: cannot advance without a pattern
  expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  await user.click(screen.getByRole("radio", { name: /Chat with your documents/ }));
  await user.click(screen.getByRole("button", { name: "Next" }));

  // Step 2: usage — knowledge step appears for document patterns
  await user.click(screen.getByRole("button", { name: /A department using it daily/ }));
  await user.click(screen.getByRole("button", { name: "Next" }));
  expect(
    screen.getByRole("heading", { name: "What should it know?" }),
  ).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Next" }));

  // Final step: region, then review
  await user.click(screen.getByRole("button", { name: "Review my answers" }));
  expect(onComplete).toHaveBeenCalledWith(
    expect.objectContaining({
      pattern: "doc_chat",
      monthly_requests: 20000,
      region: "eastus",
      currency: "USD",
    }),
  );
});

test("wizard skips the knowledge step for patterns without a knowledge base", async () => {
  const user = userEvent.setup();
  render(<RequirementsWizard patterns={patterns} onComplete={() => {}} />);
  await user.click(screen.getByRole("radio", { name: /Summarize long content/ }));
  await user.click(screen.getByRole("button", { name: "Next" }));
  await user.click(screen.getByRole("button", { name: "Next" }));
  expect(screen.queryByText("What should it know?")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Review my answers" })).toBeInTheDocument();
});

test("free-text extraction path surfaces the operator hint on failure", async () => {
  const user = userEvent.setup();
  const onExtract = vi.fn();
  render(
    <RequirementsWizard
      patterns={patterns}
      onComplete={() => {}}
      onExtract={onExtract}
      extractError="Drafting answers with AI needs operator access — the form works for everyone."
    />,
  );
  await user.type(
    screen.getByPlaceholderText(/support agents/),
    "Policy chat for 200 people",
  );
  await user.click(screen.getByRole("button", { name: /Draft the answers for me/ }));
  expect(onExtract).toHaveBeenCalledWith("Policy chat for 200 people");
  expect(screen.getByRole("alert")).toHaveTextContent("operator access");
});

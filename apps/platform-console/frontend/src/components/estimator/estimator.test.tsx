import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe, toHaveNoViolations } from "jest-axe";
import { expect, test, vi } from "vitest";
import type {
  EstimateLineItem,
  EstimateMatrix,
  EstimateTier,
  EstimatorPattern,
  TierScenarioEstimate,
} from "../../lib/types";
import { RequirementsWizard } from "./RequirementsWizard";
import { RigorSlider } from "./RigorSlider";
import { TcoMatrix } from "./TcoMatrix";
import { adjustedTotals, allEnvTotal, applyRigor, prodTotal } from "./curve";

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

function lineItemFixture(overrides: Partial<EstimateLineItem> = {}): EstimateLineItem {
  return {
    component: "serving_compute",
    env: "prod",
    tier: "production",
    scenario: "databricks",
    label: "Serving capacity (answers on demand)",
    quantity: 2_000,
    unit: "DBUs",
    unit_price: 0.05,
    currency: "USD",
    price_source: "test",
    meter_name: "test meter",
    snapshot_date: "2026-07-14",
    provenance: "test fixture",
    monthly_cost: 100,
    formula: "2,000 DBUs × $0.05",
    assumptions: [],
    is_eval_tax: false,
    eval_group: null,
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

test("TCO matrix progressively reveals compute and per-session token comparisons", async () => {
  const databricks = estimateFixture({
    scenario: "databricks",
    line_items: [
      lineItemFixture(),
      lineItemFixture({
        component: "model_tokens",
        label: "Answering requests (AI model, reading)",
        quantity: 2,
        unit: "million text units",
        monthly_cost: 4,
      }),
      lineItemFixture({
        component: "model_tokens",
        label: "Answering requests (AI model, writing)",
        quantity: 0.5,
        unit: "million text units",
        monthly_cost: 2,
      }),
    ],
  });
  const azure = estimateFixture({
    scenario: "azure",
    missing_prices: ["state_store/prod: Remembering users"],
    line_items: [
      lineItemFixture({
        scenario: "azure",
        label: "Hosting machines (Kubernetes workers)",
        quantity: 2_000,
        unit: "machine-hours",
      }),
      lineItemFixture({
        component: "model_tokens",
        scenario: "azure",
        label: "Answering requests (AI model, reading)",
        quantity: 2,
        unit: "million text units",
        monthly_cost: 4,
      }),
      lineItemFixture({
        component: "model_tokens",
        scenario: "azure",
        label: "Answering requests (AI model, writing)",
        quantity: 0.5,
        unit: "million text units",
        monthly_cost: 2,
      }),
    ],
  });
  const matrix: EstimateMatrix = {
    engine_version: "1",
    rate_card_version: "2026.07.1",
    snapshot_date: "2026-07-14",
    requirements: { monthly_requests: 1_000 },
    rigor_pct: 10,
    requirements_hash: "x".repeat(64),
    blueprint: [],
    tiers: {
      production: tierFixture({ scenarios: { databricks, azure } }),
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
  const user = userEvent.setup();
  const { container } = render(
    <TcoMatrix
      matrix={matrix}
      scenario="azure"
      rigorPct={10}
      selectedTier="production"
      onSelectTier={() => {}}
    />,
  );

  expect(screen.getByTestId("tco-summary-row")).toHaveTextContent("2,000 h");
  expect(screen.queryByTestId("tco-matrix")).not.toBeInTheDocument();
  const toggle = screen.getByRole("button", { name: "Expand detailed comparison" });
  expect(toggle).toHaveAttribute("aria-expanded", "false");

  await user.click(toggle);
  expect(screen.getByRole("button", { name: "Hide detailed comparison" })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  const table = screen.getByTestId("tco-matrix");
  expect(within(table).getByRole("columnheader", { name: /Databricks Serverless/ })).toBeVisible();
  expect(within(table).getByRole("columnheader", { name: /Azure Warehouse Compute/ })).toBeVisible();
  expect(within(table).getByRole("row", { name: /Serving compute allocation/ })).toHaveTextContent(
    "2,000 DBUs",
  );
  expect(within(table).getByRole("row", { name: /LLM input tokens/ })).toHaveTextContent("2,000");
  expect(within(table).getByRole("row", { name: /LLM output tokens/ })).toHaveTextContent("500");
  expect(screen.getByText(/1 price\(s\) unavailable/)).toBeInTheDocument();
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

// --- SimilarEstimates ---------------------------------------------------------

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SimilarEstimates } from "./SimilarEstimates";

function withQuery(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

const savedEstimate = {
  estimate_id: "e1",
  created_at: new Date().toISOString(),
  title: "Support doc chat FY27",
  pattern: "doc_chat",
  monthly_requests: 4000,
  corpus_gb: 2,
  requirements_json: '{"pattern":"doc_chat","monthly_requests":4000}',
  requirements_hash: "a".repeat(64),
  snapshot_date: "2026-07-14",
  rigor_pct: 10,
};

test("similar estimates surface exact matches and reuse past inputs", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(
        JSON.stringify({
          exact_match: savedEstimate,
          similar: [{ ...savedEstimate, estimate_id: "e2", title: "Sibling" }],
          bracket: { lo: 1000, hi: 10000 },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  );
  const onReuse = vi.fn();
  withQuery(
    <SimilarEstimates
      pattern="doc_chat"
      monthlyRequests={4000}
      requirementsHash={"a".repeat(64)}
      onReuse={onReuse}
    />,
  );
  expect(
    await screen.findByRole("heading", { name: "This estimate already exists" }),
  ).toBeInTheDocument();
  expect(screen.getByText("same inputs")).toBeInTheDocument();
  const user = userEvent.setup();
  await user.click(screen.getAllByRole("button", { name: "Use as starting point" })[0]);
  expect(onReuse).toHaveBeenCalledWith(savedEstimate);
  vi.unstubAllGlobals();
});

test("similar estimates render nothing when the library has no matches", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(
        JSON.stringify({ exact_match: null, similar: [], bracket: { lo: 1, hi: 10 } }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  );
  const { container } = withQuery(
    <SimilarEstimates pattern="doc_chat" monthlyRequests={5} onReuse={() => {}} />,
  );
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(container.querySelector("ul")).toBeNull();
  vi.unstubAllGlobals();
});

// --- DeploymentsPanel / LinkDeploymentForm ------------------------------------

import { DeploymentsPanel, LinkDeploymentForm } from "./DeploymentsPanel";

test("link deployment form reads projection server-side and posts the anchor", async () => {
  const posted: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      posted.push(JSON.parse(String(init?.body)));
      return new Response(JSON.stringify({ deployment_id: "d1", monthly_projected_usd: 900 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  const user = userEvent.setup();
  withQuery(
    <LinkDeploymentForm
      estimateId="est-1"
      tiers={["prototype", "production", "fiduciary"]}
      scenarios={["databricks", "azure"]}
    />,
  );
  await user.click(screen.getByRole("button", { name: "I deployed this" }));
  await user.type(screen.getByPlaceholderText("rg-my-solution"), "rg-doc-chat");
  await user.click(screen.getByRole("button", { name: "Link" }));
  expect(posted).toHaveLength(1);
  expect(posted[0]).toMatchObject({
    estimate_id: "est-1",
    anchor_kind: "azure_resource_group",
    anchor_value: "rg-doc-chat",
  });
  expect(posted[0]).not.toHaveProperty("monthly_projected_usd");
  vi.unstubAllGlobals();
});

// --- UnitEconomics / BudgetBar ------------------------------------------------

import { BudgetBar } from "./BudgetBar";
import { UnitEconomics } from "./UnitEconomics";

test("allEnvTotal and prodTotal read the engine's own affine totals", () => {
  // prod curve: 110 + 4×rigor; dev/uat flat at 20/30.
  expect(prodTotal(estimateFixture(), 10)).toBe(150);
  expect(allEnvTotal(estimateFixture(), 10)).toBe(200); // 20 + 30 + 150
  expect(allEnvTotal(estimateFixture(), 0)).toBe(160); // 20 + 30 + 110
});

test("unit economics divides the production total over production sessions", () => {
  render(
    <UnitEconomics estimate={estimateFixture()} rigorPct={10} monthlySessions={1000} />,
  );
  // prod total 150 / 1,000 sessions.
  expect(screen.getByText("$0.15")).toBeInTheDocument();
  expect(screen.getByText("$150")).toBeInTheDocument();
  // all-environment total 200 × 12.
  expect(screen.getByText("$2,400")).toBeInTheDocument();
});

test("unit economics refuses to divide an incomplete total into a fake unit cost", () => {
  render(
    <UnitEconomics
      estimate={estimateFixture({ missing_prices: ["state_store/prod: memory"] })}
      rigorPct={10}
      monthlySessions={1000}
    />,
  );
  expect(screen.getByText(/1 price\(s\) unavailable/)).toBeInTheDocument();
  expect(screen.queryByText("$0.15")).not.toBeInTheDocument();
});

function budgetMatrix(): EstimateMatrix {
  const cheapEnv = { total_fixed: 0, total_slope_per_pct: 0, eval_fixed: 0, eval_slope_per_pct: 0 };
  const cheap = estimateFixture({
    totals_by_env: { dev: 5, uat: 5, prod: 10 },
    rigor_curve: {
      pinned: false,
      by_env: {
        dev: { ...cheapEnv, total_fixed: 5 },
        uat: { ...cheapEnv, total_fixed: 5 },
        prod: { ...cheapEnv, total_fixed: 10 },
      },
    },
  });
  return {
    engine_version: "1",
    rate_card_version: "2026.07.1",
    snapshot_date: "2026-07-14",
    requirements: { monthly_requests: 1_000 },
    rigor_pct: 10,
    requirements_hash: "x".repeat(64),
    blueprint: [],
    tiers: {
      production: tierFixture({ scenarios: { azure: estimateFixture() } }),
      prototype: tierFixture({
        label: "Departmental prototype",
        scenarios: { azure: cheap },
      }),
    },
  };
}

test("budget bar flags over-budget spend and names the cheapest tier that fits", async () => {
  window.localStorage.clear();
  const user = userEvent.setup();
  render(
    <BudgetBar matrix={budgetMatrix()} scenario="azure" rigorPct={10} selectedTier="production" />,
  );
  // Default $5,000 budget comfortably covers the $200 total.
  expect(screen.queryByText(/over budget/)).not.toBeInTheDocument();

  const input = screen.getByRole("spinbutton");
  await user.clear(input);
  await user.type(input, "100");
  // $200 total now exceeds the $100 target by $100; prototype ($20) fits.
  expect(screen.getByText("$100 over budget")).toBeInTheDocument();
  expect(
    screen.getByText(/Departmental prototype tier fits at \$20\.00 \/ month/),
  ).toBeInTheDocument();
});

test("deployments panel lists only active links", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(
        JSON.stringify({
          data: [
            {
              deployment_id: "d1", estimate_id: "e1", created_at: new Date().toISOString(),
              tier: "production", scenario: "azure", anchor_kind: "azure_resource_group",
              anchor_value: "rg-live", monthly_projected_usd: 900, currency: "USD", active: true,
            },
            {
              deployment_id: "d2", estimate_id: "e2", created_at: new Date().toISOString(),
              tier: "prototype", scenario: "databricks", anchor_kind: "databricks_team_tag",
              anchor_value: "retired", monthly_projected_usd: 10, currency: "USD", active: false,
            },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  );
  withQuery(<DeploymentsPanel />);
  expect(await screen.findByText(/rg-live/)).toBeInTheDocument();
  expect(screen.queryByText(/retired/)).not.toBeInTheDocument();
  vi.unstubAllGlobals();
});

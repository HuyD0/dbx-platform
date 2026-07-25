import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { CostControl } from "./CostControl";
import { CostValue } from "./CostValue";

vi.mock("../components/CostTrendChart", () => ({
  CostTrendChart: () => <div data-testid="cost-trend-chart" />,
  costCategoryColor: (category: string) =>
    ({ Databricks: "rgb(10, 20, 30)", Storage: "rgb(40, 50, 60)" })[
      category
    ] ?? "rgb(70, 80, 90)",
}));

const alignment = {
  status: "variances_found",
  variance_count: 3,
  unmatched_count: 1,
  latest_azure_date: "2026-07-21",
  latest_databricks_date: "2026-07-20",
  azure_lag_days: 1,
  databricks_lag_days: 2,
  azure_totals: [{ cost: 120, currency: "CAD", cost_basis: "AZURE_ACTUAL" }],
  databricks_totals: [
    { cost: 88, currency: "USD", cost_basis: "DATABRICKS_LIST" },
  ],
  largest_pattern_variance: {
    usage_date: "2026-07-19",
    sku_family: "JOBS",
    delta_pct_points: 14.2,
  },
  money_comparable: false,
  notes: "Coverage and spend-shape alignment.",
};

const overview = {
  scope: {
    label: "prod workspace",
    workspace_id: "740",
    environment: "prod",
    resource_groups: ["platform-rg", "managed-rg"],
  },
  period: { days: 30, from: "2026-06-22", to: "2026-07-21" },
  totals: [
    {
      currency: "CAD",
      cost: 123.45,
      previous_period_cost: 100,
      period_delta_pct: 23.45,
      cost_basis: "AZURE_ACTUAL",
    },
  ],
  components: [
    {
      component: "Azure Databricks",
      cost: 120,
      currency: "CAD",
      cost_basis: "AZURE_ACTUAL",
      share_pct: 97.2,
    },
    {
      component: "Other Azure infrastructure",
      cost: 3.45,
      currency: "CAD",
      cost_basis: "AZURE_ACTUAL",
      share_pct: 2.8,
    },
  ],
  series: [
    {
      usage_date: "2026-07-20",
      category: "Databricks",
      currency: "CAD",
      cost: 10,
      cost_basis: "AZURE_ACTUAL",
    },
  ],
  categories: [
    {
      category: "Databricks",
      cost: 120,
      currency: "CAD",
      cost_basis: "AZURE_ACTUAL",
      share_pct: 97.2,
    },
    {
      category: "Storage",
      cost: 3.45,
      currency: "CAD",
      cost_basis: "AZURE_ACTUAL",
      share_pct: 2.8,
    },
  ],
  ownership: [],
  movers: Array.from({ length: 5 }, (_, index) => ({
    category: `Mover ${index + 1}`,
    cost: 10,
    previous_cost: 5,
    change: 5,
    change_pct: 100,
    currency: "CAD",
    cost_basis: "AZURE_ACTUAL",
    share_pct: 10,
  })),
  anomalies: Array.from({ length: 5 }, (_, index) => ({
    id: `signal-${index + 1}`,
    signal: "Daily spike",
    day: "2026-07-20",
    category: "Databricks",
    currency: "CAD",
    cost_basis: "AZURE_ACTUAL",
    cost: 20 + index,
    baseline: 10,
    change_pct: 100,
    severity: "serious",
    reason: `Signal reason ${index + 1}`,
  })),
  databricks_list: {
    cost: 88,
    currency: "USD",
    cost_basis: "DATABRICKS_LIST",
    additive_to_total: false,
    rows: [],
    status: "available",
    notes: "List price only.",
  },
  billing_alignment: alignment,
  data_health: [{ source: "Azure", status: "healthy", freshness: "1h" }],
};

function envelope(data: unknown) {
  return new Response(
    JSON.stringify({
      data,
      count: Array.isArray(data) ? data.length : null,
      as_of: "2026-07-22T12:00:00Z",
      cached: false,
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

function renderPage(page: ReactNode, route = "/") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={[route]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        {page}
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("Cost Control shows full counts, compact previews, and distinct legend colors", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => envelope(overview)));
  renderPage(<CostControl />);

  expect(await screen.findByText("Variance watch")).toBeVisible();
  expect(screen.getByText("View all 5 signals")).toBeVisible();
  expect(screen.getByText("Signal reason 1").closest("a")).toHaveClass("flex");
  expect(screen.getByText("Signal reason 3").closest("a")).toHaveClass(
    "hidden",
    "sm:flex",
  );
  expect(screen.getByText("Mover 4").closest("a")).toHaveClass("hidden", "sm:flex");

  const databricksLegend = screen.getByText("Databricks 97.2%");
  const storageLegend = screen.getByText("Storage 2.8%");
  expect(databricksLegend.querySelector("span")).toHaveStyle({
    backgroundColor: "rgb(10, 20, 30)",
  });
  expect(storageLegend.querySelector("span")).toHaveStyle({
    backgroundColor: "rgb(40, 50, 60)",
  });
});

test("Billing Alignment filters the variance register without comparing currencies", async () => {
  const rows = [
    {
      usage_date: "2026-07-20",
      sku_family: "SQL",
      databricks_list_usd: null,
      azure_billed_cost: 12,
      azure_currency: "CAD",
      comparison_status: "AZURE_ONLY",
      classifications: ["AZURE_ONLY"],
      evaluated: true,
      money_comparable: false,
      pattern_delta_pct_points: null,
      variance: null,
      variance_pct: null,
      azure_meters: ["SQL Compute"],
      databricks_skus: null,
    },
    {
      usage_date: "2026-07-19",
      sku_family: "JOBS",
      databricks_list_usd: 10,
      azure_billed_cost: 18,
      azure_currency: "CAD",
      comparison_status: "PATTERN_VARIANCE",
      classifications: ["PATTERN_VARIANCE", "BASIS_MISMATCH"],
      evaluated: true,
      money_comparable: false,
      pattern_delta_pct_points: 14.2,
      variance: null,
      variance_pct: null,
      azure_meters: ["Jobs Compute"],
      databricks_skus: ["JOBS_COMPUTE"],
    },
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) =>
      String(input).startsWith("/api/cost/reconciliation")
        ? envelope(rows)
        : envelope(overview),
    ),
  );
  const user = userEvent.setup();
  renderPage(<CostValue />, "/cost?tab=alignment");

  expect(await screen.findByText("Where variance exists")).toBeVisible();
  expect(screen.getByText("CA$120")).toBeVisible();
  expect(screen.getByText("$88.00")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Azure Only" }));
  expect(screen.getByText("SQL")).toBeVisible();
  expect(screen.queryByText("JOBS")).not.toBeInTheDocument();
});

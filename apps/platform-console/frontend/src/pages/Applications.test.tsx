import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe, toHaveNoViolations } from "jest-axe";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { ApplicationDetail } from "./ApplicationDetail";
import { Applications } from "./Applications";

expect.extend(toHaveNoViolations);

function envelope(data: unknown, count: number | null = null) {
  return new Response(
    JSON.stringify({
      data,
      count,
      as_of: "2026-07-25T16:00:00Z",
      cached: false,
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

function renderRoute(entry: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={[entry]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/applications" element={<Applications />} />
          <Route
            path="/applications/:applicationKey"
            element={<ApplicationDetail />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const portfolio = {
  applications: [
    {
      application_key: "study-portal",
      display_name: "Study Portal",
      environments: ["dev"],
      sources: ["azure", "databricks"],
      ledgers: [
        {
          id: "azure-CAD-AZURE_ACTUAL",
          source: "azure",
          title: "Azure Actual",
          amount: 43.52,
          currency: "CAD",
          pricing_basis: "AZURE_ACTUAL",
          attributed_cost: 43.52,
          unallocated_cost: 11,
          coverage_start: "2026-06-26",
          coverage_end: "2026-07-25",
          freshness: "2026-07-25T15:00:00Z",
        },
        {
          id: "databricks-USD-DATABRICKS_LIST",
          source: "databricks",
          title: "Databricks List",
          amount: 15.84,
          currency: "USD",
          pricing_basis: "DATABRICKS_LIST",
          attributed_cost: 15.84,
          unallocated_cost: 0,
          coverage_start: "2026-06-26",
          coverage_end: "2026-07-25",
          freshness: "2026-07-25T15:00:00Z",
        },
      ],
      trend_pct: 4.2,
      tag_health: {
        status: "attention",
        matched: 8,
        missing: 2,
        conflicts: 1,
      },
      coverage_pct: 82,
      last_evidence_at: "2026-07-25T15:00:00Z",
    },
  ],
  facets: {
    environments: ["dev", "prod"],
    sources: ["azure", "databricks"],
  },
  next_cursor: null,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

test("portfolio keeps pricing ledgers separate and persists filters in the URL", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn(async (_input: RequestInfo | URL) =>
    envelope(portfolio, 1),
  );
  vi.stubGlobal("fetch", fetchMock);

  renderRoute("/applications?window=90d&environment=dev");

  expect(
    await screen.findByRole("heading", { name: "Study Portal" }),
  ).toBeInTheDocument();
  expect(screen.getByText("CA$43.52")).toBeInTheDocument();
  expect(screen.getByText("$15.84")).toBeInTheDocument();
  expect(screen.queryByText(/59\.36/)).not.toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: /Study Portal/ }),
  ).toHaveAttribute(
    "href",
    "/applications/study-portal?window=90d",
  );

  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("window=90d"),
      expect.anything(),
    ),
  );
  expect(String(fetchMock.mock.calls[0]?.[0])).toContain("environment=dev");

  await user.selectOptions(
    screen.getByRole("combobox", { name: "Source" }),
    "azure",
  );
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("source=azure"),
      expect.anything(),
    ),
  );
});

const profile = {
  application: {
    application_key: "study-portal",
    display_name: "Study Portal",
    environments: ["dev"],
    sources: ["azure", "databricks"],
    last_evidence_at: "2026-07-25T15:00:00Z",
  },
  period: {
    window: "30d",
    days: 30,
    start: "2026-06-26",
    end: "2026-07-25",
  },
  ledgers: portfolio.applications[0]?.ledgers ?? [],
  series: [
    {
      usage_date: "2026-07-24",
      ledger_id: "azure-CAD-AZURE_ACTUAL",
      source: "azure",
      currency: "CAD",
      pricing_basis: "AZURE_ACTUAL",
      cost: 4.5,
    },
    {
      usage_date: "2026-07-24",
      ledger_id: "databricks-USD-DATABRICKS_LIST",
      source: "databricks",
      currency: "USD",
      pricing_basis: "DATABRICKS_LIST",
      cost: 2.1,
    },
  ],
  drivers: [
    {
      source: "databricks",
      ledger_id: "databricks-USD-DATABRICKS_LIST",
      dimension: "workload",
      name: "Daily ingestion",
      resource_type: "job",
      resource_id: "job-12",
      cost: 8.2,
      currency: "USD",
      pricing_basis: "DATABRICKS_LIST",
      attribution_method: "DIRECT_TAG",
    },
  ],
  tag_alignment: [
    {
      source: "databricks",
      resource_name: "Daily ingestion",
      tag_key: "project",
      raw_value: "Study-Portal",
      normalized_value: "study-portal",
      status: "matched",
    },
    {
      source: "azure",
      resource_name: "shared-host",
      tag_key: null,
      raw_value: null,
      normalized_value: null,
      status: "missing",
    },
    {
      source: "azure",
      resource_name: "model-endpoint",
      tag_key: "application",
      raw_value: "another-app",
      normalized_value: "another-app",
      status: "conflict",
    },
  ],
  coverage: [
    {
      source: "azure",
      status: "partial",
      attributed_rows: 6,
      total_rows: 10,
      attributed_cost: 43.52,
      total_cost: 54.52,
      currency: "CAD",
      pricing_basis: "AZURE_ACTUAL",
      coverage_pct: 60,
    },
    {
      source: "databricks",
      status: "healthy",
      attributed_rows: 9,
      total_rows: 10,
      attributed_cost: 15.84,
      total_cost: 15.84,
      currency: "USD",
      pricing_basis: "DATABRICKS_LIST",
      coverage_pct: 90,
    },
  ],
  unallocated: [
    {
      source: "azure",
      reason: "Shared resource has no accepted application tag.",
      cost: 11,
      currency: "CAD",
      pricing_basis: "AZURE_ACTUAL",
      row_count: 4,
    },
  ],
  source_health: [
    {
      source: "Azure Cost Management",
      status: "partial",
      last_success_at: "2026-07-25T15:00:00Z",
      coverage_start: "2026-06-26",
      coverage_end: "2026-07-25",
      notes: "One shared resource remains unallocated.",
    },
    {
      source: "Databricks billing",
      status: "healthy",
      last_success_at: "2026-07-25T15:00:00Z",
      coverage_start: "2026-06-26",
      coverage_end: "2026-07-25",
      notes: "Current.",
    },
  ],
};

const evidence = {
  items: [
    {
      evidence_id: "evidence-1",
      usage_date: "2026-07-24",
      source: "databricks",
      environment: "dev",
      resource_type: "job",
      resource_id: "job-12",
      resource_name: "Daily ingestion",
      service: "Jobs",
      workload: "Daily ingestion",
      application_key: "study-portal",
      raw_application: "Study-Portal",
      attribution_method: "DIRECT_TAG",
      tag_key: "project",
      tags: { project: "Study-Portal" },
      cost: 8.2,
      currency: "USD",
      pricing_basis: "DATABRICKS_LIST",
      evidence_at: "2026-07-25T15:00:00Z",
      scope: "workspace:123",
      conflict_values: [],
    },
    {
      evidence_id: "inventory-1",
      usage_date: "2026-07-24",
      source: "databricks",
      environment: "dev",
      resource_type: "app",
      resource_id: "app-1",
      resource_name: "Study Portal",
      service: "Databricks Apps inventory",
      workload: "APP_INVENTORY",
      application_key: "study-portal",
      raw_application: "Study Portal",
      attribution_method: "DIRECT_METADATA",
      cost: 0,
      cost_known: false,
      inventory_only: true,
      evidence_kind: "inventory_only",
      currency: "USD",
      pricing_basis: "DATABRICKS_LIST",
      evidence_at: "2026-07-25T15:00:00Z",
      scope: "workspace:123",
      conflict_values: [],
    },
  ],
  next_cursor: null,
};

test("profile shows exact, unallocated, tag, source-health, and raw evidence states", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/evidence")) return envelope(evidence, 1);
    return envelope(profile);
  });
  vi.stubGlobal("fetch", fetchMock);

  renderRoute("/applications/study-portal?window=30d");

  expect(
    await screen.findByRole("heading", { name: "Study Portal" }),
  ).toBeInTheDocument();
  expect(screen.getAllByText("CA$43.52").length).toBeGreaterThan(0);
  expect(screen.getAllByText("$15.84").length).toBeGreaterThan(0);
  expect(screen.queryByText(/59\.36/)).not.toBeInTheDocument();
  expect(
    screen.getByText(
      "Each card preserves its source currency and pricing basis. These amounts may overlap and are never combined into one cross-cloud total.",
    ),
  ).toBeInTheDocument();
  expect(
    screen.getByText("Shared resource has no accepted application tag."),
  ).toBeInTheDocument();
  expect(screen.getByText("1 matched")).toBeInTheDocument();
  expect(
    screen.getByText(
      "One or more sources are partial, stale, or unavailable. Exact totals include only collected evidence.",
    ),
  ).toBeInTheDocument();

  await user.click(screen.getByText("Open evidence"));
  expect((await screen.findAllByText("workspace:123")).length).toBeGreaterThan(0);
  expect(screen.getByText("Not measured")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Export all CSV" })).toHaveAttribute(
    "href",
    expect.stringContaining("format=csv"),
  );
});

test("stale ledgers remain auditable without being presented as a current exact claim", async () => {
  const staleProfile = {
    ...profile,
    ledgers: profile.ledgers.map((ledger, index) =>
      index === 0
        ? { ...ledger, trusted: false, status: "stale", scope: ["subscription:sub-1"] }
        : ledger,
    ),
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input: RequestInfo | URL) => envelope(staleProfile)),
  );

  renderRoute("/applications/study-portal");

  expect(
    await screen.findByRole("heading", { name: "Attributed cost evidence" }),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/visible for audit but is not a current exact claim/i),
  ).toBeInTheDocument();
  expect(screen.getByText("subscription:sub-1")).toBeInTheDocument();
});

test("application portfolio has no automated accessibility violations", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input: RequestInfo | URL) => envelope(portfolio, 1)),
  );
  const rendered = renderRoute("/applications");
  expect(
    await screen.findByRole("heading", { name: "Study Portal" }),
  ).toBeInTheDocument();
  expect(await axe(rendered.container)).toHaveNoViolations();
});

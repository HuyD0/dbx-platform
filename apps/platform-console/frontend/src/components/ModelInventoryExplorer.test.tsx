import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { ModelInventoryExplorer } from "./ModelInventoryExplorer";

const managedModel = {
  source: "databricks_uc",
  model_key: "uc:main.ml.learn_agent",
  model_name: "main.ml.learn_agent",
  model_version: "3",
  entity_type: "REGISTERED_MODEL",
  provider: "databricks",
  environment: "prod",
  owner: "alice@example.com",
  status: "READY",
  ownership: "customer_managed",
  needs_attention: false,
  risk: "clear",
  risk_reasons: [],
  exposure: [],
  group_key: "databricks_uc:main.ml",
  group_label: "main.ml",
};

const summary = {
  total: 84,
  customer_managed: 2,
  system: 82,
  needs_attention: 1,
  key_auth_exposed: 1,
  groups_on_page: 1,
};

function inventoryResponse({
  items = [managedModel],
  sourceHealth = [
    {
      source: "databricks_uc",
      status: "healthy",
      notes: "Unity Catalog inventory is current.",
    },
  ],
}: {
  items?: Array<Record<string, unknown>>;
  sourceHealth?: Array<Record<string, unknown>>;
} = {}) {
  return new Response(
    JSON.stringify({
      data: {
        items,
        total: items.length,
        next_cursor: null,
        facets: {
          source: [{ value: "databricks_uc", count: 84 }],
          provider: [{ value: "databricks", count: 84 }],
          environment: [{ value: "prod", count: 84 }],
          entity_type: [{ value: "REGISTERED_MODEL", count: 84 }],
          owner: [{ value: "alice@example.com", count: 1 }],
          status: [{ value: "READY", count: 84 }],
          exposure: [{ value: "key_auth", count: 1 }],
          risk: [{ value: "attention", count: 1 }],
        },
        summary,
        source_health: sourceHealth,
      },
      count: items.length,
      as_of: "2026-07-25T16:00:00Z",
      cached: false,
      next_cursor: null,
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

function detailResponse() {
  return new Response(
    JSON.stringify({
      data: {
        entity: managedModel,
        access: [
          {
            principal_name: "ml-engineers",
            access_level: "INVOKE",
            via: "DIRECT",
          },
        ],
        source_health: [
          {
            source: "databricks_uc",
            status: "healthy",
          },
        ],
      },
      count: 1,
      as_of: "2026-07-25T16:00:00Z",
      cached: false,
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

function renderExplorer(entry = "/ai-governance") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={[entry]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <ModelInventoryExplorer />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function requestUrls(fetchMock: ReturnType<typeof vi.fn>): URL[] {
  return fetchMock.mock.calls.map(
    ([input]) => new URL(String(input), "https://console.test"),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("requests the managed-or-risky inventory view by default", async () => {
  const fetchMock = vi.fn(async () => inventoryResponse());
  vi.stubGlobal("fetch", fetchMock);

  renderExplorer();

  expect(await screen.findByText("main.ml.learn_agent")).toBeVisible();
  const request = requestUrls(fetchMock).find(
    (url) => url.pathname === "/api/ai-governance/inventory",
  );
  expect(request?.searchParams.get("view")).toBe("managed_or_risky");
  expect(request?.searchParams.get("limit")).toBe("50");
});

test("switches to the complete server view when system models are included", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn(async () => inventoryResponse());
  vi.stubGlobal("fetch", fetchMock);
  renderExplorer();
  await screen.findByText("main.ml.learn_agent");

  await user.click(
    screen.getByRole("checkbox", { name: /include all databricks system models/i }),
  );

  await waitFor(() =>
    expect(
      requestUrls(fetchMock).some(
        (url) =>
          url.pathname === "/api/ai-governance/inventory" &&
          url.searchParams.get("view") === "all",
      ),
    ).toBe(true),
  );
});

test("hydrates filters and a deep-linked model detail from the URL", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "https://console.test");
    if (url.pathname.startsWith("/api/ai-governance/inventory/")) {
      return detailResponse();
    }
    return inventoryResponse();
  });
  vi.stubGlobal("fetch", fetchMock);

  renderExplorer(
    "/ai-governance?q=learn&source=databricks_uc&environment=prod&risk=attention&model=uc%3Amain.ml.learn_agent",
  );

  const dialog = await screen.findByRole("dialog");
  expect(
    await within(dialog).findByRole("heading", { name: "main.ml.learn_agent" }),
  ).toBeVisible();
  expect(screen.getByRole("searchbox", { name: "Search model inventory" })).toHaveValue(
    "learn",
  );
  expect(screen.getByRole("combobox", { name: "Source" })).toHaveValue("databricks_uc");
  expect(screen.getByRole("combobox", { name: "Environment" })).toHaveValue("prod");

  await waitFor(() => {
    const requests = requestUrls(fetchMock);
    const inventory = requests.find(
      (url) => url.pathname === "/api/ai-governance/inventory",
    );
    expect(inventory?.searchParams.get("q")).toBe("learn");
    expect(inventory?.searchParams.get("source")).toBe("databricks_uc");
    expect(inventory?.searchParams.get("environment")).toBe("prod");
    expect(inventory?.searchParams.get("risk")).toBe("attention");
    expect(
      requests.some(
        (url) =>
          decodeURIComponent(url.pathname) ===
          "/api/ai-governance/inventory/uc:main.ml.learn_agent",
      ),
    ).toBe(true);
  });
});

test("builds a complete server CSV link without page cursor or limit", async () => {
  const fetchMock = vi.fn(async () => inventoryResponse());
  vi.stubGlobal("fetch", fetchMock);
  renderExplorer(
    "/ai-governance?q=learn&source=databricks_uc&system=1&inventoryCursor=opaque",
  );

  const link = await screen.findByRole("link", { name: "Export complete CSV" });
  const exportUrl = new URL(link.getAttribute("href") ?? "", "https://console.test");
  expect(exportUrl.pathname).toBe("/api/ai-governance/inventory");
  expect(exportUrl.searchParams.get("format")).toBe("csv");
  expect(exportUrl.searchParams.get("view")).toBe("all");
  expect(exportUrl.searchParams.get("q")).toBe("learn");
  expect(exportUrl.searchParams.get("source")).toBe("databricks_uc");
  expect(exportUrl.searchParams.has("cursor")).toBe(false);
  expect(exportUrl.searchParams.has("limit")).toBe(false);
});

test("keeps partial source health and its evidence note visible", async () => {
  const fetchMock = vi.fn(async () =>
    inventoryResponse({
      sourceHealth: [
        {
          source: "databricks_uc",
          status: "partial",
          notes: "84 Unity Catalog grants could not be read.",
        },
        {
          source: "azure_openai",
          status: "unavailable",
          notes: "Azure Resource Graph permission is unavailable.",
        },
      ],
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  renderExplorer();

  const coverage = await screen.findByLabelText("Inventory source coverage");
  expect(within(coverage).getByText("partial")).toBeVisible();
  expect(within(coverage).getByText("unavailable")).toBeVisible();
  expect(
    within(coverage).getByText("84 Unity Catalog grants could not be read."),
  ).toBeVisible();
  expect(
    within(coverage).getByText("Azure Resource Graph permission is unavailable."),
  ).toBeVisible();
});

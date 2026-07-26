import AxeBuilder from "@axe-core/playwright";
import {
  expect,
  test,
  type Page,
  type Route,
  type TestInfo,
} from "@playwright/test";
import type {
  ModelInventoryResponse,
  ModelInventoryRow,
} from "../src/lib/modelInventory";
import type {
  ApplicationEnvelope,
  ApplicationPortfolio,
  ApplicationProfile,
} from "../src/types/applications";

type Theme = "light" | "dark";

const FIXED_NOW = new Date("2026-07-26T16:00:00.000Z");

const portfolio = {
  data: {
    applications: [
      {
        application_key: "study-portal",
        display_name: "Study Portal",
        environments: ["production"],
        sources: ["azure", "databricks"],
        ledgers: [
          {
            id: "azure-CAD-AZURE_ACTUAL",
            source: "azure",
            title: "Azure billed actual",
            amount: 43.52,
            currency: "CAD",
            pricing_basis: "AZURE_ACTUAL",
            attributed_cost: 43.52,
            unallocated_cost: 11,
            coverage_start: "2026-06-27",
            coverage_end: "2026-07-26",
            freshness: "2026-07-26T15:00:00.000Z",
            trusted: true,
          },
          {
            id: "databricks-USD-DATABRICKS_LIST",
            source: "databricks",
            title: "Databricks list price",
            amount: 15.84,
            currency: "USD",
            pricing_basis: "DATABRICKS_LIST",
            attributed_cost: 15.84,
            unallocated_cost: 0,
            coverage_start: "2026-06-27",
            coverage_end: "2026-07-26",
            freshness: "2026-07-26T15:00:00.000Z",
            trusted: true,
          },
        ],
        trend_pct: 4.2,
        tag_health: {
          status: "attention",
          matched: 1,
          missing: 1,
          conflicts: 1,
        },
        coverage_pct: 82,
        last_evidence_at: "2026-07-26T15:00:00.000Z",
      },
    ],
    facets: {
      environments: ["production"],
      sources: ["azure", "databricks"],
    },
    next_cursor: null,
  },
  count: 1,
  as_of: "2026-07-26T16:00:00.000Z",
  cached: false,
  source_status: {
    source: "application_cost_evidence",
    status: "healthy",
  },
} satisfies ApplicationEnvelope<ApplicationPortfolio>;

const profile = {
  data: {
    application: {
      application_key: "study-portal",
      display_name: "Study Portal",
      environments: ["production"],
      sources: ["azure", "databricks"],
      last_evidence_at: "2026-07-26T15:00:00.000Z",
    },
    period: {
      window: "30d",
      days: 30,
      start: "2026-06-27",
      end: "2026-07-26",
    },
    ledgers: portfolio.data.applications[0].ledgers,
    series: [
      {
        usage_date: "2026-07-25",
        ledger_id: "azure-CAD-AZURE_ACTUAL",
        cost: 4.5,
        currency: "CAD",
        pricing_basis: "AZURE_ACTUAL",
      },
      {
        usage_date: "2026-07-25",
        ledger_id: "databricks-USD-DATABRICKS_LIST",
        cost: 2.1,
        currency: "USD",
        pricing_basis: "DATABRICKS_LIST",
      },
    ],
    drivers: [
      {
        ledger_id: "azure-CAD-AZURE_ACTUAL",
        source: "azure",
        dimension: "resource",
        name: "study-portal-api",
        resource_type: "App Service",
        resource_id: "/subscriptions/masked/resourceGroups/apps/providers/web/study-portal",
        service: "App Service",
        workload: "API",
        cost: 24.25,
        currency: "CAD",
        pricing_basis: "AZURE_ACTUAL",
        attribution_method: "DIRECT_TAG",
      },
      {
        ledger_id: "databricks-USD-DATABRICKS_LIST",
        source: "databricks",
        dimension: "workload",
        name: "Daily ingestion",
        resource_type: "job",
        resource_id: "job-12",
        service: "Jobs",
        workload: "Daily ingestion",
        cost: 8.2,
        currency: "USD",
        pricing_basis: "DATABRICKS_LIST",
        attribution_method: "DIRECT_TAG",
      },
    ],
    tag_alignment: [
      {
        source: "databricks",
        resource_id: "job-12",
        resource_name: "Daily ingestion",
        tag_key: "project",
        raw_value: "Study-Portal",
        normalized_value: "study-portal",
        status: "matched",
      },
      {
        source: "azure",
        resource_id: "/subscriptions/masked/resourceGroups/shared/providers/web/shared-host",
        resource_name: "shared-host",
        tag_key: null,
        raw_value: null,
        normalized_value: null,
        status: "missing",
      },
      {
        source: "azure",
        resource_id: "/subscriptions/masked/resourceGroups/ai/providers/cognitive/model-endpoint",
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
        currency: "CAD",
        pricing_basis: "AZURE_ACTUAL",
        attributed_rows: 6,
        total_rows: 10,
        attributed_cost: 43.52,
        total_cost: 54.52,
        coverage_pct: 60,
      },
      {
        source: "databricks",
        status: "healthy",
        currency: "USD",
        pricing_basis: "DATABRICKS_LIST",
        attributed_rows: 9,
        total_rows: 10,
        attributed_cost: 15.84,
        total_cost: 15.84,
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
        last_success_at: "2026-07-26T15:00:00.000Z",
        coverage_start: "2026-06-27",
        coverage_end: "2026-07-26",
        notes: "One shared resource remains unallocated.",
      },
      {
        source: "Databricks billing",
        status: "healthy",
        last_success_at: "2026-07-26T15:00:00.000Z",
        coverage_start: "2026-06-27",
        coverage_end: "2026-07-26",
        notes: "Current.",
      },
    ],
  },
  count: 1,
  as_of: "2026-07-26T16:00:00.000Z",
  cached: false,
} satisfies ApplicationEnvelope<ApplicationProfile>;

const customerModels = [
  {
    source: "databricks_uc",
    model_key: "uc:main.ml.churn",
    model_name: "main.ml.churn",
    model_version: "7",
    entity_type: "REGISTERED_MODEL",
    provider: "databricks",
    environment: "production",
    owner: "ml-platform@example.test",
    status: "READY",
    region: "canadacentral",
    ownership: "customer_managed",
    needs_attention: false,
    risk: "clear",
    risk_reasons: [],
    exposure: [],
    access_count: 1,
    group_key: "databricks_uc:main.ml",
    group_label: "main.ml",
    first_seen_at: "2026-06-01T12:00:00.000Z",
    last_seen_at: "2026-07-26T15:00:00.000Z",
    is_current: true,
  },
  {
    source: "azure_openai",
    model_key:
      "/subscriptions/masked/resourceGroups/ai/providers/Microsoft.CognitiveServices/accounts/prod-ai/deployments/support-gpt",
    model_name: "support-gpt",
    endpoint_name: "support-gpt",
    entity_type: "AZURE_OPENAI_DEPLOYMENT",
    provider: "azure",
    environment: "production",
    owner: "support-ai@example.test",
    status: "READY",
    region: "canadaeast",
    resource_group: "ai",
    ownership: "customer_managed",
    needs_attention: true,
    risk: "attention",
    risk_reasons: ["Key auth enabled"],
    exposure: ["key_auth"],
    key_auth_enabled: true,
    usage_tracking: true,
    group_key: "azure_openai:prod-ai",
    group_label: "prod-ai",
  },
] satisfies ModelInventoryRow[];

const systemModels = [
  {
    source: "databricks_uc",
    model_key: "uc:system.ai.forecasting",
    model_name: "system.ai.forecasting",
    model_version: "1",
    entity_type: "REGISTERED_MODEL",
    provider: "databricks",
    environment: "system",
    owner: "Databricks",
    status: "READY",
    ownership: "system",
    needs_attention: false,
    risk: "clear",
    risk_reasons: [],
    exposure: [],
    group_key: "databricks_uc:system.ai",
    group_label: "system.ai",
  },
  {
    source: "databricks_uc",
    model_key: "uc:system.ai.text_embedding",
    model_name: "system.ai.text_embedding",
    model_version: "1",
    entity_type: "REGISTERED_MODEL",
    provider: "databricks",
    environment: "system",
    owner: "Databricks",
    status: "READY",
    ownership: "system",
    needs_attention: false,
    risk: "clear",
    risk_reasons: [],
    exposure: [],
    group_key: "databricks_uc:system.ai",
    group_label: "system.ai",
  },
] satisfies ModelInventoryRow[];

const inventoryFacets = {
  source: [
    { value: "databricks_uc", count: 3 },
    { value: "azure_openai", count: 1 },
  ],
  provider: [
    { value: "databricks", count: 3 },
    { value: "azure", count: 1 },
  ],
  environment: [
    { value: "production", count: 2 },
    { value: "system", count: 2 },
  ],
  entity_type: [
    { value: "REGISTERED_MODEL", count: 3 },
    { value: "AZURE_OPENAI_DEPLOYMENT", count: 1 },
  ],
  owner: [
    { value: "ml-platform@example.test", count: 1 },
    { value: "support-ai@example.test", count: 1 },
    { value: "Databricks", count: 2 },
  ],
  status: [{ value: "READY", count: 4 }],
  exposure: [{ value: "key_auth", count: 1 }],
  risk: [
    { value: "attention", count: 1 },
    { value: "clear", count: 3 },
  ],
};

function inventoryResponse(includeSystem: boolean): ModelInventoryResponse {
  const items = includeSystem
    ? [...customerModels, ...systemModels]
    : customerModels;
  return {
    data: {
      items,
      total: items.length,
      next_cursor: null,
      truncated: false,
      facets: inventoryFacets,
      summary: {
        total: 4,
        customer_managed: 2,
        system: 2,
        needs_attention: 1,
        key_auth_exposed: 1,
        groups_on_page: includeSystem ? 3 : 2,
      },
      source_health: [
        {
          source: "databricks_uc",
          status: "healthy",
          notes: "Unity Catalog inventory is current.",
        },
        {
          source: "azure_openai",
          status: "healthy",
          notes: "Azure AI inventory is current.",
        },
      ],
    },
    count: items.length,
    as_of: "2026-07-26T16:00:00.000Z",
    cached: false,
  };
}

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function preparePage(page: Page, testInfo: TestInfo) {
  const metadata = testInfo.project.metadata as { theme?: unknown };
  if (metadata.theme !== "light" && metadata.theme !== "dark") {
    throw new Error(`Invalid Playwright theme metadata: ${JSON.stringify(metadata)}`);
  }
  const theme: Theme = metadata.theme;
  await page.clock.setFixedTime(FIXED_NOW);
  await page.addInitScript((selectedTheme: Theme) => {
    localStorage.setItem("theme", selectedTheme);
  }, theme);
}

async function expectTheme(page: Page, testInfo: TestInfo) {
  const theme = (testInfo.project.metadata as { theme: Theme }).theme;
  await expect
    .poll(() =>
      page.evaluate(() =>
        document.documentElement.classList.contains("dark") ? "dark" : "light",
      ),
    )
    .toBe(theme);
}

async function expectNoSeriousAxeViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  const severe = results.violations
    .filter((violation) => ["serious", "critical"].includes(violation.impact ?? ""))
    .map((violation) => ({
      id: violation.id,
      impact: violation.impact,
      help: violation.help,
      targets: violation.nodes.map((node) => node.target),
    }));
  expect(severe).toEqual([]);
}

async function mockCommonApi(page: Page) {
  await page.route("**/api/health", (route) =>
    json(route, {
      status: "ok",
      version: "e2e",
      environment: "production",
      actions_enabled: false,
    }),
  );
  await page.route("**/api/context", (route) =>
    json(route, {
      workspace_name: "Production analytics",
      workspace_id: "workspace-1",
      environment: "production",
      roles: ["viewer"],
      actions_enabled: false,
    }),
  );
}

async function mockApplicationApi(page: Page) {
  await mockCommonApi(page);
  await page.route("**/api/applications**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/applications") {
      await json(route, portfolio);
      return;
    }
    if (path === "/api/applications/study-portal") {
      await json(route, profile);
      return;
    }
    await json(route, { error: "not_found", message: path }, 404);
  });
}

async function mockModelApi(page: Page) {
  const requestedViews: string[] = [];
  await mockCommonApi(page);
  await page.route("**/api/ai-governance/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/ai-governance/compliance") {
      await json(route, {
        data: {
          metrics: [],
          zdr_alerts: [],
          unverified_zdr_resources: 0,
          evaluated_resources: 2,
        },
        count: 2,
        as_of: "2026-07-26T16:00:00.000Z",
        cached: false,
      });
      return;
    }
    if (url.pathname === "/api/ai-governance/inventory") {
      const view = url.searchParams.get("view") ?? "";
      requestedViews.push(view);
      await json(route, inventoryResponse(view === "all"));
      return;
    }
    if (
      decodeURIComponent(url.pathname) ===
      "/api/ai-governance/inventory/uc:main.ml.churn"
    ) {
      await json(route, {
        data: {
          entity: customerModels[0],
          access: [
            {
              principal_name: "ml-engineers",
              access_level: "EXECUTE",
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
        as_of: "2026-07-26T16:00:00.000Z",
        cached: false,
      });
      return;
    }
    await json(route, { error: "not_found", message: url.pathname }, 404);
  });
  return requestedViews;
}

test("application portfolio and detail keep ledgers and attribution gaps separate", async ({
  page,
}, testInfo) => {
  await preparePage(page, testInfo);
  await mockApplicationApi(page);
  await page.goto("/applications");
  await expectTheme(page, testInfo);

  const portfolioRegion = page.getByRole("region", {
    name: "Application portfolio",
  });
  await expect(
    portfolioRegion.getByRole("heading", { name: "Study Portal" }),
  ).toBeVisible();
  await expect(portfolioRegion.getByText("Azure billed actual")).toBeVisible();
  await expect(portfolioRegion.getByText("CA$43.52")).toBeVisible();
  await expect(portfolioRegion.getByText("AZURE ACTUAL")).toBeVisible();
  await expect(portfolioRegion.getByText("Databricks list price")).toBeVisible();
  await expect(portfolioRegion.getByText("$15.84")).toBeVisible();
  await expect(
    portfolioRegion.getByText("DATABRICKS LIST", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/59\.36/)).toHaveCount(0);
  await expectNoSeriousAxeViolations(page);

  await portfolioRegion.getByRole("link", { name: /Study Portal/ }).click();
  await expect(
    page.getByRole("heading", { name: "Study Portal", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(
      "Each card preserves its source currency and pricing basis. These amounts may overlap and are never combined into one cross-cloud total.",
    ),
  ).toBeVisible();
  await expect(page.getByText("CA$43.52").first()).toBeVisible();
  await expect(page.getByText("$15.84").first()).toBeVisible();
  await expect(page.getByText(/59\.36/)).toHaveCount(0);

  const unallocated = page.getByRole("list", {
    name: "Costs outside exact attribution",
  });
  await expect(
    unallocated.getByText("Shared resource has no accepted application tag."),
  ).toBeVisible();
  await expect(unallocated.getByText("CA$11.00")).toBeVisible();
  await expect(unallocated.getByText("Outside exact total")).toBeVisible();

  const tagSummary = page.getByLabel("Tag status summary");
  await expect(tagSummary.getByText("1 matched")).toBeVisible();
  await expect(tagSummary.getByText("1 missing")).toBeVisible();
  await expect(tagSummary.getByText("1 conflicts")).toBeVisible();
  const tagMatrix = page.getByRole("table", {
    name: "Tag values joined across Azure and Databricks",
  });
  await expect(tagMatrix.getByRole("row")).toHaveCount(4);
  await expect(tagMatrix.getByText("Daily ingestion")).toBeVisible();
  await expect(tagMatrix.getByText("shared-host")).toBeVisible();
  await expect(tagMatrix.getByText("model-endpoint")).toBeVisible();
  await expectNoSeriousAxeViolations(page);
});

test("model inventory defaults to two customer models and restores focus after Escape", async ({
  page,
}, testInfo) => {
  await preparePage(page, testInfo);
  const requestedViews = await mockModelApi(page);
  await page.goto("/ai-governance");
  await expectTheme(page, testInfo);

  const systemToggle = page.getByRole("checkbox", {
    name: /Include all Databricks system models/,
  });
  await expect(systemToggle).not.toBeChecked();
  await expect(page.getByText("2 matching entities · 2 groups on this page")).toBeVisible();
  await expect(page.getByText("main.ml.churn", { exact: true })).toBeVisible();
  await expect(page.getByText("support-gpt", { exact: true })).toBeVisible();
  await expect(page.getByText("system.ai.forecasting", { exact: true })).toHaveCount(0);
  await expect
    .poll(() => requestedViews.includes("managed_or_risky"))
    .toBe(true);
  await expectNoSeriousAxeViolations(page);

  await systemToggle.check();
  await expect(page).toHaveURL(/(?:\?|&)system=1(?:&|$)/);
  await expect(page.getByText("4 matching entities · 3 groups on this page")).toBeVisible();
  await expect(page.getByText("system.ai.forecasting", { exact: true })).toBeVisible();
  await expect(page.getByText("system.ai.text_embedding", { exact: true })).toBeVisible();
  await expect.poll(() => requestedViews.includes("all")).toBe(true);

  const trigger = page.locator(
    'button[data-inventory-key="uc:main.ml.churn"]',
  );
  await trigger.focus();
  await expect(trigger).toBeFocused();
  await page.keyboard.press("Enter");

  const drawer = page.getByRole("dialog", { name: "main.ml.churn" });
  await expect(drawer).toBeVisible();
  const close = drawer.getByRole("button", { name: "Close model details" });
  await expect(close).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(drawer.getByRole("tab", { name: "Raw evidence" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(close).toBeFocused();
  await expectNoSeriousAxeViolations(page);

  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(trigger).toBeFocused();
  await expect(page).not.toHaveURL(/(?:\?|&)model=/);
});

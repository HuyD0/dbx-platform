import { expect, test, type Page, type Route, type TestInfo } from "@playwright/test";

const overview = {
  data: {
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
      {
        usage_date: "2026-07-20",
        category: "Storage",
        currency: "CAD",
        cost: 2,
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
    billing_alignment: {
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
    },
    data_health: [{ source: "Azure", status: "healthy", freshness: "1h" }],
  },
  count: null,
  as_of: "2026-07-22T12:00:00Z",
  cached: false,
};

const reconciliation = {
  data: [
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
  ],
  count: 1,
  as_of: "2026-07-22T12:00:00Z",
  cached: false,
};

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function mockCostApi(page: Page) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/health") {
      await json(route, {
        status: "ok",
        version: "e2e",
        environment: "prod",
        actions_enabled: false,
      });
    } else if (path === "/api/cost/overview") {
      await json(route, overview);
    } else if (path === "/api/cost/reconciliation") {
      await json(route, reconciliation);
    } else {
      await json(route, { error: "not_found", message: path }, 404);
    }
  });
}

async function assertNoPageOverflow(page: Page) {
  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth + 1);
}

test("Cost Control and Billing Alignment reflow without clipping", async ({
  page,
}, testInfo: TestInfo) => {
  const width = Number((testInfo.project.metadata as { width?: number }).width);
  test.skip(width > 768, "Focused mobile and reflow coverage.");
  await mockCostApi(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Cost Control" })).toBeVisible();
  await assertNoPageOverflow(page);

  const dots = page.getByTestId("cost-legend-dot");
  expect(await dots.count()).toBe(2);
  const colors = await dots.evaluateAll((elements) =>
    elements.map(
      (element) => getComputedStyle(element as HTMLElement).backgroundColor,
    ),
  );
  expect(new Set(colors).size).toBe(2);

  if (width <= 375) {
    await expect(page.getByText("Signal reason 1")).toBeVisible();
    await expect(page.getByText("Signal reason 2")).toBeVisible();
    await expect(page.getByText("Signal reason 3")).toBeHidden();
  }

  await page.goto("/cost?tab=alignment");
  await expect(page.getByText("Where variance exists")).toBeVisible();
  await expect(page.getByText("SQL Compute")).toBeVisible();
  await assertNoPageOverflow(page);
});

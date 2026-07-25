import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe, toHaveNoViolations } from "jest-axe";
import { expect, test } from "vitest";
import {
  ProductSpendBreakdown,
  aggregateProductSpend,
  buildCostAttributionFlows,
  buildCurrencySankeys,
} from "./ProductSpendBreakdown";

expect.extend(toHaveNoViolations);

const rows = [
  {
    period: "current",
    product: "APPS",
    resource_type: "app",
    resource_name: "platform-console",
    sku_name: "PREMIUM_APPS_SERVERLESS_COMPUTE",
    usage_quantity: "4",
    usage_unit: "DBU",
    list_cost_usd: "40",
    tags: { cost_center: "platform", env: "prod" },
  },
  {
    period: "previous",
    product: "APPS",
    resource_type: "app",
    resource_name: "platform-console",
    sku_name: "PREMIUM_APPS_SERVERLESS_COMPUTE",
    usage_quantity: "2",
    usage_unit: "DBU",
    list_cost_usd: "20",
  },
  {
    period: "current",
    product: "DATABASE",
    resource_type: "database",
    resource_name: "orders-db",
    sku_name: "PREMIUM_DATABASE_SERVERLESS_COMPUTE",
    usage_quantity: "3",
    usage_unit: "DBU",
    list_cost_usd: "30",
  },
];

test("aggregates billing origins into readable product totals", () => {
  expect(aggregateProductSpend(rows)).toEqual([
    { key: "APPS", label: "Databricks Apps", current: 40, previous: 20 },
    { key: "LAKEBASE", label: "Lakebase", current: 30, previous: 0 },
  ]);
});

test("shows product share and an accessible workload drill-down", async () => {
  const user = userEvent.setup();
  render(<ProductSpendBreakdown rows={rows} days={30} />);

  expect(screen.getByText("$70.00")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Databricks Apps/ })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(screen.getByText("platform-console")).toBeInTheDocument();
  expect(screen.getByText("cost_center: platform, env: prod")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: /Lakebase/ }));
  expect(screen.getByRole("button", { name: /Lakebase/ })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(screen.getByText("orders-db")).toBeInTheDocument();
});

test("builds cross-cloud source-to-initiative-to-deployment cost flows", () => {
  const databricksRows = [
    {
      period: "current",
      product: "MODEL_SERVING",
      project: "investment_analytics",
      endpoint_name: "portfolio-risk-endpoint",
      usage_unit: "DBU",
      list_cost_usd: "120",
    },
    {
      period: "current",
      product: "JOBS",
      project: "investment_analytics",
      resource_name: "ordinary-etl-job",
      list_cost_usd: "900",
    },
    {
      period: "previous",
      product: "MODEL_SERVING",
      project: "investment_analytics",
      endpoint_name: "portfolio-risk-endpoint",
      list_cost_usd: "500",
    },
  ];
  const foundryRows = [
    {
      resource_id:
        "/subscriptions/s/resourceGroups/rg-ms-iva/providers/Microsoft.CognitiveServices/accounts/aoai-prod",
      resource_group: "rg-ms-iva",
      resource_type: "Microsoft.CognitiveServices/accounts",
      meter_name: "gpt-5 input tokens",
      cost: 80,
      currency: "CAD",
    },
    {
      resource_id:
        "/subscriptions/s/resourceGroups/rg-ms-iva/providers/Microsoft.CognitiveServices/accounts/aoai-prod",
      resource_group: "rg-ms-iva",
      resource_type: "Microsoft.CognitiveServices/accounts",
      meter_name: "gpt-5 output tokens",
      cost: 20,
      currency: "CAD",
    },
    {
      resource_id:
        "/subscriptions/s/resourceGroups/rg-ms-iva/providers/Microsoft.CognitiveServices/accounts/aoai-prod",
      resource_group: "rg-ms-iva",
      resource_type: "Microsoft.CognitiveServices/accounts",
      meter_name: "provisioned throughput unit",
      cost: 999,
      currency: "CAD",
    },
  ];

  const flows = buildCostAttributionFlows(databricksRows, foundryRows);
  expect(flows).toEqual([
    {
      key: "CAD\u0000Microsoft Foundry API Tokens\u0000MS-IVA\u0000gpt-5 · aoai-prod",
      source: "Microsoft Foundry API Tokens",
      initiative: "MS-IVA",
      target: "gpt-5 · aoai-prod",
      cost: 100,
      currency: "CAD",
    },
    {
      key:
        "USD\u0000Databricks DBUs\u0000Investment Analytics\u0000portfolio-risk-endpoint · Model serving",
      source: "Databricks DBUs",
      initiative: "Investment Analytics",
      target: "portfolio-risk-endpoint · Model serving",
      cost: 120,
      currency: "USD",
    },
  ]);
  expect(flows.some((flow) => flow.target.includes("ordinary-etl-job"))).toBe(false);

  const partitions = buildCurrencySankeys(flows);
  expect(partitions.map((partition) => [partition.currency, partition.total])).toEqual([
    ["CAD", 100],
    ["USD", 120],
  ]);
  expect(partitions[0].sourceLinks).toEqual([
    {
      key: "Microsoft Foundry API Tokens\u0000MS-IVA",
      from: "Microsoft Foundry API Tokens",
      to: "MS-IVA",
      cost: 100,
    },
  ]);
});

test("renders aggregated currency-safe attribution with both infrastructure sources", async () => {
  const { container } = render(
    <ProductSpendBreakdown
      days={30}
      rows={[
        ...rows,
        {
          period: "current",
          product: "MODEL_SERVING",
          project: "investment_analytics",
          endpoint_name: "portfolio-risk-endpoint",
          usage_unit: "DBU",
          list_cost_usd: "15",
        },
      ]}
      foundrySource={{
        status: "ready",
        rows: [
          {
            resource_id:
              "/subscriptions/s/resourceGroups/rg-ms-iva/providers/Microsoft.CognitiveServices/accounts/aoai-prod",
            resource_group: "rg-ms-iva",
            meter_name: "gpt-5 output tokens",
            cost: 25,
            currency: "CAD",
          },
        ],
      }}
    />,
  );

  const chart = screen.getByTestId("cost-attribution-flow");
  expect(screen.getAllByText("Databricks DBUs").length).toBeGreaterThan(0);
  expect(screen.getAllByText("Microsoft Foundry API Tokens").length).toBeGreaterThan(0);
  expect(screen.getAllByText("MS-IVA").length).toBeGreaterThan(0);
  expect(screen.getAllByText("gpt-5 · aoai-prod").length).toBeGreaterThan(0);
  expect(screen.getByRole("region", { name: "CAD cost attribution" })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "USD cost attribution" })).toBeInTheDocument();
  expect(within(chart).getAllByText(/CA\$25\.00/).length).toBeGreaterThan(0);
  expect(within(chart).queryByText("platform-console · Databricks Apps")).not.toBeInTheDocument();
  expect(
    screen.getAllByRole("heading", { name: "Deployed models / indexes" }).length,
  ).toBe(2);
  expect(await axe(container)).toHaveNoViolations();
});

test("makes Foundry loading, unavailable, and error states explicit", () => {
  const { rerender } = render(
    <ProductSpendBreakdown
      rows={rows}
      days={30}
      foundrySource={{ status: "loading", rows: [] }}
    />,
  );
  expect(screen.getByText("Loading persisted Azure resource and meter actuals.")).toBeVisible();

  rerender(
    <ProductSpendBreakdown
      rows={rows}
      days={30}
      foundrySource={{
        status: "unavailable",
        rows: [],
        message: "No current Azure billing scope is persisted.",
      }}
    />,
  );
  expect(screen.getByText("No current Azure billing scope is persisted.")).toBeVisible();

  rerender(
    <ProductSpendBreakdown
      rows={rows}
      days={30}
      foundrySource={{ status: "error", rows: [] }}
    />,
  );
  expect(screen.getByText("Foundry actuals could not be read.")).toBeVisible();
});

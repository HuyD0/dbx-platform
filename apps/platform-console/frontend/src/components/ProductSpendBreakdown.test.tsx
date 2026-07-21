import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { ProductSpendBreakdown, aggregateProductSpend } from "./ProductSpendBreakdown";

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

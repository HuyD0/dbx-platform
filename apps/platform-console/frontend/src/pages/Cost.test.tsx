import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { Cost } from "./Cost";

function response(data: unknown[], sourceStatus?: Record<string, unknown>) {
  return new Response(
    JSON.stringify({
      data,
      count: data.length,
      as_of: "2026-07-20T12:00:00Z",
      cached: false,
      source_status: sourceStatus,
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

test("Cost loads persisted Foundry attribution even when Databricks product spend is empty", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/cost/foundry-attribution")) {
      return response(
        [
          {
            resource_id:
              "/subscriptions/s/resourceGroups/rg-ms-iva/providers/Microsoft.CognitiveServices/accounts/aoai-prod",
            resource_group: "rg-ms-iva",
            resource_type: "Microsoft.CognitiveServices/accounts",
            meter_name: "gpt-5 input tokens",
            cost: 31.5,
            currency: "CAD",
            cost_basis: "AZURE_ACTUAL",
          },
        ],
        {
          status: "healthy",
          source: "Azure Cost Management · azure_cost_details",
          notes: "Persisted token-meter actuals are available.",
        },
      );
    }
    return response([]);
  });
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={client}>
      <Cost />
    </QueryClientProvider>,
  );

  expect(await screen.findByRole("region", { name: "CAD cost attribution" })).toBeVisible();
  expect(screen.getAllByText("gpt-5 · aoai-prod").length).toBeGreaterThan(0);
  expect(screen.getByText("Persisted token-meter actuals are available.")).toBeVisible();
  expect(
    fetchMock.mock.calls.some(([input]) =>
      String(input).startsWith("/api/cost/foundry-attribution?days=30"),
    ),
  ).toBe(true);
});

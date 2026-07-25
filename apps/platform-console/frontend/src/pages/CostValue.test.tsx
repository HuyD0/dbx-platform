import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { Cost } from "./Cost";
import { CostValue } from "./CostValue";

function envelope(data: unknown[]) {
  return new Response(
    JSON.stringify({
      data,
      as_of: "2026-07-25T00:00:00Z",
      cached: false,
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

function renderPage(page: ReactNode, entry = "/cost") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={[entry]}
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

test("Databricks spend switches from AI product to project attribution", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).startsWith("/api/cost/usage")) {
        return envelope([
          {
            workload_type: "AGENT_EVALUATION",
            sku_name: "PREMIUM_SERVERLESS_JOBS_COMPUTE",
            project: "agent-eval",
            app: "agent-eval",
            list_cost_usd: 12.5,
          },
        ]);
      }
      return envelope([]);
    }),
  );

  renderPage(<Cost />);

  expect(await screen.findByText("AGENT_EVALUATION")).toBeInTheDocument();
  await user.selectOptions(screen.getByRole("combobox", { name: "Dimension" }), "project");
  expect(await screen.findByText("agent-eval")).toBeInTheDocument();
});

test("Azure actuals can switch from service to exact resource detail", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/cost/azure?") && url.includes("by=resource")) {
      return envelope([
        {
          resource_id: "/subscriptions/sub/resourceGroups/rg/providers/type/agent-eval",
          cost: 4.25,
          currency: "CAD",
        },
      ]);
    }
    if (url.startsWith("/api/cost/azure?")) {
      return envelope([{ service_name: "Azure Databricks", cost: 10, currency: "CAD" }]);
    }
    return envelope([]);
  });
  vi.stubGlobal("fetch", fetchMock);

  renderPage(<CostValue />, "/cost?tab=azure");

  expect(await screen.findByText("Azure Databricks")).toBeInTheDocument();
  await user.selectOptions(screen.getByRole("combobox", { name: "Dimension" }), "resource");
  expect(
    await screen.findByText(
      "/subscriptions/sub/resourceGroups/rg/providers/type/agent-eval",
    ),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("by=resource"),
      expect.anything(),
    ),
  );
});

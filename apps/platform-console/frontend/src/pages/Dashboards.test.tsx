import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { Dashboards } from "./Dashboards";

const dashboardResponse = {
  data: [
    {
      name: "[dbx-platform] Cost & Usage",
      url: "https://adb.example/sql/dashboardsv3/cost?o=123",
      embed_url: "https://adb.example/embed/dashboardsv3/cost?o=123",
    },
    {
      name: "[dbx-platform] Job Operations",
      url: "https://adb.example/sql/dashboardsv3/jobs?o=123",
      embed_url: "https://adb.example/embed/dashboardsv3/jobs?o=123",
    },
  ],
  count: 2,
  as_of: "2026-07-25T12:00:00Z",
  cached: false,
};

test("keeps the workspace fallback visible and switches the embedded dashboard", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(JSON.stringify(dashboardResponse), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const user = userEvent.setup();

  render(
    <QueryClientProvider client={queryClient}>
      <Dashboards />
    </QueryClientProvider>,
  );

  expect(await screen.findByTitle("[dbx-platform] Cost & Usage")).toHaveAttribute(
    "src",
    "https://adb.example/embed/dashboardsv3/cost?o=123",
  );
  expect(screen.getByRole("link", { name: "Open in workspace" })).toHaveAttribute(
    "href",
    "https://adb.example/sql/dashboardsv3/cost?o=123",
  );
  expect(screen.getByText(/Allow third-party cookies/)).toBeVisible();

  await user.click(screen.getByRole("button", { name: "Job Operations" }));

  expect(screen.getByTitle("[dbx-platform] Job Operations")).toHaveAttribute(
    "src",
    "https://adb.example/embed/dashboardsv3/jobs?o=123",
  );
  expect(screen.getByRole("link", { name: "Open in workspace" })).toHaveAttribute(
    "href",
    "https://adb.example/sql/dashboardsv3/jobs?o=123",
  );
});

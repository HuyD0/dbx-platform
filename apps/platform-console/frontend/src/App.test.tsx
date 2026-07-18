import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { expect, test, vi } from "vitest";
import App from "./App";

function renderApp() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={["/"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("skip link and mobile drawer are keyboard complete", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).startsWith("/api/health")) {
        return new Response(
          JSON.stringify({
            status: "ok",
            version: "test",
            environment: "dev",
            actions_enabled: false,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(
        JSON.stringify({
          error: "dependency_unavailable",
          message: "Test source unavailable.",
        }),
        { status: 503, headers: { "Content-Type": "application/json" } },
      );
    }),
  );
  renderApp();

  await user.click(screen.getByRole("link", { name: "Skip to main content" }));
  expect(screen.getByRole("main")).toHaveFocus();

  const trigger = screen.getByRole("button", { name: "Open navigation" });
  await user.click(trigger);
  const drawer = screen.getByRole("dialog", { name: "Mobile navigation" });
  expect(drawer).toHaveAttribute("aria-modal", "true");
  const close = screen.getByRole("button", { name: "Close navigation" });
  await waitFor(() => expect(close).toHaveFocus());

  close.focus();
  await user.tab({ shift: true });
  expect(
    within(drawer).getByRole("button", {
      name: /Switch to (?:light|dark) theme/,
    }),
  ).toHaveFocus();

  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog", { name: "Mobile navigation" })).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

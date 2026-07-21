import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { expect, test, vi } from "vitest";
import { ChatProvider } from "../lib/chat";
import { AssistantPanel } from "./AssistantPanel";

function setViewport(desktop: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: desktop && query === "(min-width: 640px)",
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={["/security?severity=critical"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <ChatProvider>
          <AssistantPanel open onClose={vi.fn()} />
        </ChatProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("mobile context memory isolates the covered chat and restores focus on close", async () => {
  setViewport(false);
  const user = userEvent.setup();
  renderPanel();

  expect(screen.getByRole("dialog", { name: "Read-only investigator" })).toBeInTheDocument();
  const toggle = screen.getByRole("button", { name: "Show context memory" });
  expect(toggle).toHaveAttribute("aria-expanded", "false");

  await user.click(toggle);

  const chatSurface = screen.getByTestId("assistant-chat-surface");
  expect(chatSurface).toHaveAttribute("inert");
  expect(chatSurface).toHaveAttribute("aria-hidden", "true");
  expect(screen.getByRole("heading", { name: "Context memory" })).toBeInTheDocument();
  expect(screen.getByText("Active bounded constraints")).toBeInTheDocument();
  expect(screen.getByText("0 messages active · 50-message request ceiling")).toBeInTheDocument();
  expect(screen.getByText("/security")).toBeInTheDocument();
  expect(screen.getByText("1 of 30 filter slots · 0 of 20 selected resources")).toBeInTheDocument();
  expect(screen.getByText("Evidence and proposals only")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Close context memory" })).toHaveFocus();
  await user.tab();
  expect(chatSurface).not.toContainElement(document.activeElement as HTMLElement);

  await user.click(screen.getByRole("button", { name: "Close context memory" }));
  expect(screen.queryByRole("heading", { name: "Context memory" })).not.toBeInTheDocument();
  const restoredToggle = screen.getByRole("button", { name: "Show context memory" });
  expect(restoredToggle).toHaveAttribute("aria-expanded", "false");
  expect(restoredToggle).toHaveFocus();
  expect(chatSurface).not.toHaveAttribute("inert");
  expect(chatSurface).not.toHaveAttribute("aria-hidden");
});

test("desktop context memory preserves the interactive split-pane chat", async () => {
  setViewport(true);
  const user = userEvent.setup();
  renderPanel();

  await user.click(screen.getByRole("button", { name: "Show context memory" }));

  const chatSurface = screen.getByTestId("assistant-chat-surface");
  expect(chatSurface).not.toHaveAttribute("inert");
  expect(chatSurface).not.toHaveAttribute("aria-hidden");
  expect(screen.getByRole("textbox", { name: "Message the platform agent" })).toBeInTheDocument();
});

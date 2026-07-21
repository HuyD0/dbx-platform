import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { expect, test, vi } from "vitest";
import { ChatProvider, useChat, type ChatFocus } from "../lib/chat";
import { ChatThread } from "./ChatThread";

const focus: ChatFocus = {
  actionId: "action-policy-1",
  label: "Synchronize managed policies",
};

function Harness() {
  const { send, setFocus } = useChat();
  return (
    <div className="h-[40rem]">
      <button
        type="button"
        onClick={() => {
          setFocus(focus);
          send("Explain this exact plan.", focus);
        }}
      >
        Ask focused
      </button>
      <button type="button" onClick={() => setFocus(null)}>
        Clear focus
      </button>
      <ChatThread compact />
    </div>
  );
}

function renderThread() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={["/"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <ChatProvider>
          <Harness />
        </ChatProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("sends focused action context and preserves structured citations after focus clears", async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    new Response(
      JSON.stringify({
        message: "The current finding supports this exact plan.",
        proposals: [],
        citations: [
          {
            citation_id: "evidence-1",
            tool: "get_canonical_findings",
            source: "canonical platform_findings",
            observed_at: "2026-07-18T12:00:00Z",
          },
        ],
        endpoint: "test-agent",
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderThread();

  await user.click(screen.getByRole("button", { name: "Ask focused" }));

  expect(
    await screen.findByText("The current finding supports this exact plan."),
  ).toBeInTheDocument();
  expect(screen.getByText("Sources cited")).toBeInTheDocument();
  expect(screen.getByText("canonical platform_findings")).toBeInTheDocument();
  expect(screen.getByText(/get_canonical_findings/)).toBeInTheDocument();
  const observed = screen.getByTitle("2026-07-18T12:00:00Z");
  expect(observed).toHaveAttribute("datetime", "2026-07-18T12:00:00Z");

  const request = fetchMock.mock.calls[0];
  const body = JSON.parse(String(request?.[1]?.body));
  expect(body.context.focus_action_id).toBe(focus.actionId);

  await user.click(screen.getByRole("button", { name: "Clear focus" }));
  expect(screen.getByText("canonical platform_findings")).toBeInTheDocument();
});

test("focused answers explicitly disclose a missing structured citation", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(
        JSON.stringify({
          message: "No grounded marker was returned.",
          proposals: [],
          citations: [],
          endpoint: "test-agent",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  );
  const user = userEvent.setup();
  renderThread();

  await user.click(screen.getByRole("button", { name: "Ask focused" }));

  expect(
    await screen.findByText(
      "No structured source citation was returned for this focused answer.",
    ),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(screen.getByText("No grounded marker was returned.")).toBeInTheDocument(),
  );
});

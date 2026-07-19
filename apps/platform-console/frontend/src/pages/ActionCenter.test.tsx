import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe, toHaveNoViolations } from "jest-axe";
import { afterEach, expect, test, vi } from "vitest";
import { ActionCenter } from "./ActionCenter";

expect.extend(toHaveNoViolations);

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderActionCenter() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ActionCenter />
    </QueryClientProvider>,
  );
}

test("tabs use effective status and expose approval readiness", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(
        JSON.stringify({
          data: [
            {
              action_id: "active-1",
              action_type: "runtime.hibernate",
              status: "AWAITING_APPROVAL",
              effective_status: "AWAITING_APPROVAL",
              can_approve: true,
              evaluated_at: "2026-07-18T12:05:00Z",
              expires_at: "2026-07-18T12:15:00Z",
              created_at: "2026-07-18T12:00:00Z",
              risk: "medium",
              targets: [{ resource_type: "JOB", resource_id: "101" }],
              proposer_email: "operator@example.com",
              plan_hash: "a".repeat(64),
            },
            {
              action_id: "missing-readiness-1",
              action_type: "runtime.wake",
              status: "AWAITING_APPROVAL",
              raw_status: "AWAITING_APPROVAL",
              effective_status: "AWAITING_APPROVAL",
              evaluated_at: "2026-07-18T12:05:00Z",
              expires_at: "2026-07-18T12:15:00Z",
              created_at: "2026-07-18T12:00:00Z",
              risk: "low",
              targets: [{ resource_type: "APP", resource_id: "platform-console" }],
              proposer_email: "operator@example.com",
              plan_hash: "d".repeat(64),
            },
            {
              action_id: "expired-1",
              action_type: "policy-sync",
              status: "AWAITING_APPROVAL",
              effective_status: "EXPIRED",
              can_approve: false,
              evaluated_at: "2026-07-18T12:16:00Z",
              expires_at: "2026-07-18T12:15:00Z",
              created_at: "2026-07-18T12:00:00Z",
              risk: "high",
              targets: [{ resource_type: "POLICY", resource_id: "policy-1" }],
              proposer_email: "operator@example.com",
              plan_hash: "b".repeat(64),
            },
            {
              action_id: "legacy-expired-1",
              action_type: "run-job",
              status: "AWAITING_APPROVAL",
              can_approve: false,
              evaluated_at: "2026-07-18T12:20:00Z",
              expires_at: "2026-07-18T12:15:00Z",
              created_at: "2026-07-18T12:00:00Z",
              risk: "low",
              targets: [{ resource_type: "JOB", resource_id: "202" }],
              proposer_email: "operator@example.com",
              plan_hash: "c".repeat(64),
            },
          ],
          count: 4,
          as_of: "2026-07-18T12:16:00Z",
          cached: false,
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    ),
  );

  const rendered = renderActionCenter();
  const approvalTab = await screen.findByRole("tab", { name: /Awaiting approval 2/i });
  const failedTab = screen.getByRole("tab", { name: /Failed \/ rolled back 2/i });

  await user.click(approvalTab);
  expect(await screen.findAllByText("AWAITING APPROVAL")).toHaveLength(2);
  expect(
    screen.getByRole("button", { name: "Review approval runtime.hibernate" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Review action runtime.wake" }),
  ).toBeInTheDocument();

  await user.click(failedTab);
  expect(await screen.findAllByText("EXPIRED")).toHaveLength(2);
  expect(screen.queryByText("AWAITING APPROVAL")).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Review action policy-sync" }),
  ).toBeInTheDocument();
  expect(await axe(rendered.container)).toHaveNoViolations();
});

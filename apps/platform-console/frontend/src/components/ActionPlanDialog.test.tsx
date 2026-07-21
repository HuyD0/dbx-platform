import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { ActionPlanDialog } from "./ActionPlanDialog";

test("job plan explains the decision and requires a separate confirmation click", async () => {
  const user = userEvent.setup();
  const plan = {
    plan_id: "plan-1",
    action: "run-job",
    expires_at: Date.now() + 15 * 60 * 1000,
    items: [
      {
        resource_type: "JOB",
        resource_id: "7",
        job_id: 7,
        name: "[dbx-platform] platform-digest",
        action: "RUN_NOW",
        settings_sha256: "b".repeat(64),
      },
    ],
    summary: { run: 1 },
    confirm_phrase: "apply run-job 1",
    actions_enabled: true,
    plan_hash: "a".repeat(64),
    risk: "low",
    impact: { summary: { run: 1 }, target_count: 1 },
    rollback: {
      supported: false,
      description: "A started job run cannot be automatically rolled back.",
    },
    verification: {
      strategy: "Re-read the target after execution and record its resulting state.",
    },
  };
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.body && String(init.body).includes('"plan_hash"')) {
      return new Response(
        JSON.stringify({ plan_id: "plan-1", action: "run-job", status: "APPROVED" }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    return new Response(JSON.stringify(plan), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  render(
    <QueryClientProvider client={queryClient}>
      <ActionPlanDialog
        action="run-job"
        title="Plan fresh digest"
        allowLegacy={false}
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  );

  expect(
    await screen.findByRole("heading", {
      name: "Run [dbx-platform] platform-digest once?",
    }),
  ).toBeInTheDocument();
  expect(
    screen.getByText("This starts one new run now. It does not change the job or its schedule."),
  ).toBeInTheDocument();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Approve and run once" }));
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("alertdialog", { name: "Confirm approval" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Confirm and run once" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  const approvalBody = JSON.parse(String(fetchMock.mock.calls[1][1]?.body));
  expect(approvalBody).toEqual({ plan_hash: plan.plan_hash });
});

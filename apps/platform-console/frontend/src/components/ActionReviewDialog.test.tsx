import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe, toHaveNoViolations } from "jest-axe";
import { expect, test, vi } from "vitest";
import { ActionReviewDialog } from "./ActionReviewDialog";

expect.extend(toHaveNoViolations);

const action = {
  action_id: "action-1",
  action_type: "runtime.hibernate",
  plan_hash: "a".repeat(64),
  confirm_phrase: "apply runtime.hibernate 2",
  status: "AWAITING_APPROVAL",
  risk: "MEDIUM",
  actions_enabled: true,
  expires_at: "2026-07-18T12:15:00Z",
  targets: [
    { resource_type: "JOB", resource_id: "101" },
    { resource_type: "APP", resource_id: "platform-console" },
  ],
  impact: { changed_resource_count: 2 },
  rollback: { strategy: "restore-exact-before-state" },
  verification: { exact_states: true },
  events: [],
};

test("approval dialog traps/restores focus and requires exact confirmation", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(JSON.stringify(action), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
  const onClose = vi.fn();
  const trigger = document.createElement("button");
  trigger.textContent = "Review";
  document.body.append(trigger);
  trigger.focus();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const rendered = render(
    <QueryClientProvider client={queryClient}>
      <ActionReviewDialog actionId="action-1" onClose={onClose} />
    </QueryClientProvider>,
  );

  const dialog = await screen.findByRole("dialog", { name: "Review action request" });
  expect(dialog).toHaveAttribute("aria-modal", "true");
  const close = screen.getByRole("button", { name: "Close action review" });
  await waitFor(() => expect(close).toHaveFocus());

  const approve = screen.getByRole("button", { name: "Approve exact plan" });
  expect(approve).toBeDisabled();
  await user.type(
    screen.getByLabelText(/Type apply runtime\.hibernate 2/),
    action.confirm_phrase,
  );
  expect(approve).toBeEnabled();

  close.focus();
  await user.tab({ shift: true });
  expect(screen.getByRole("button", { name: "Reject plan" })).toHaveFocus();

  expect(await axe(dialog)).toHaveNoViolations();
  await user.keyboard("{Escape}");
  expect(onClose).toHaveBeenCalledOnce();

  rendered.unmount();
  expect(trigger).toHaveFocus();
  trigger.remove();
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe, toHaveNoViolations } from "jest-axe";
import { afterEach, expect, test, vi } from "vitest";
import type { ActionRequestDetail } from "../lib/types";
import { ActionReviewDialog } from "./ActionReviewDialog";

expect.extend(toHaveNoViolations);

const action: ActionRequestDetail = {
  schema_version: 1,
  action_id: "action-1",
  action_type: "runtime.hibernate",
  workspace_id: "workspace-1",
  environment: "production",
  parameters: {},
  preconditions: {},
  before_state: null,
  after_state: null,
  plan_hash: "a".repeat(64),
  confirm_phrase: "apply runtime.hibernate 2",
  status: "AWAITING_APPROVAL",
  raw_status: "AWAITING_APPROVAL",
  effective_status: "AWAITING_APPROVAL",
  can_approve: true,
  evaluated_at: "2026-07-18T12:05:00Z",
  risk: "medium",
  actions_enabled: true,
  approver_required: true,
  proposer_id: "masked-proposer",
  proposer_email: "operator@example.test",
  created_at: "2026-07-18T12:00:00Z",
  expires_at: "2026-07-18T12:15:00Z",
  updated_at: "2026-07-18T12:05:00Z",
  terminal_reason: null,
  idempotency_key: "idempotency-action-1",
  targets: [
    { resource_type: "JOB", resource_id: "101" },
    { resource_type: "APP", resource_id: "platform-console" },
  ],
  plan_id: "action-1",
  action: "runtime.hibernate",
  items: [
    { resource_type: "JOB", resource_id: "101" },
    { resource_type: "APP", resource_id: "platform-console" },
  ],
  summary: { changed_resource_count: 2 },
  impact: { changed_resource_count: 2 },
  rollback: { strategy: "restore-exact-before-state" },
  verification: { exact_states: true },
  evidence_correlation: {
    coverage_status: "NO_MATCH",
    total: 0,
    truncated: false,
    items: [],
  },
  approvals: [],
  events: [],
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

test("approval dialog traps/restores focus and uses a separate confirmation step", async () => {
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

  const approve = screen.getByRole("button", { name: "Approve action" });
  expect(approve).toBeEnabled();
  expect(screen.queryByLabelText(/Type apply runtime\.hibernate 2/)).not.toBeInTheDocument();
  expect(screen.getByText("Plan review guide")).toBeInTheDocument();
  expect(screen.getByText(/Start with the plain-language summary/i)).toBeInTheDocument();
  expect(screen.getByText("changed resource count")).toBeInTheDocument();

  close.focus();
  await user.tab({ shift: true });
  expect(screen.getByRole("button", { name: "Reject plan" })).toHaveFocus();

  await user.click(approve);
  expect(screen.getByRole("alertdialog", { name: "Confirm approval" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Confirm approval" })).toBeEnabled();

  expect(await axe(dialog)).toHaveNoViolations();
  await user.keyboard("{Escape}");
  expect(onClose).toHaveBeenCalledOnce();

  rendered.unmount();
  expect(trigger).toHaveFocus();
  trigger.remove();
});

test("expired effective status fails closed and explains how to continue", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(
        JSON.stringify({
          ...action,
          effective_status: "EXPIRED",
          can_approve: false,
          evaluated_at: "2026-07-18T12:16:00Z",
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    ),
  );
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const rendered = render(
    <QueryClientProvider client={queryClient}>
      <ActionReviewDialog actionId="action-1" onClose={vi.fn()} />
    </QueryClientProvider>,
  );

  const dialog = await screen.findByRole("dialog", { name: "Review action request" });
  expect(await screen.findByText("EXPIRED")).toBeInTheDocument();
  expect(screen.getByText(/Ledger status: AWAITING APPROVAL/i)).toBeInTheDocument();
  expect(screen.getByText(/evaluated/i)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Approve action" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Reject plan" })).not.toBeInTheDocument();
  expect(
    screen.getByText(/expired and cannot be approved or replayed/i),
  ).toBeInTheDocument();
  expect(await axe(dialog)).toHaveNoViolations();

  rendered.unmount();
});

test("an approval confirmation closes exactly when the server-anchored plan expires", async () => {
  const boundaryAction: ActionRequestDetail = {
    ...action,
    evaluated_at: "2026-07-18T12:14:59.750Z",
    expires_at: "2026-07-18T12:15:00.000Z",
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(JSON.stringify(boundaryAction), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
  const user = userEvent.setup();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <ActionReviewDialog actionId="action-1" onClose={vi.fn()} />
    </QueryClientProvider>,
  );

  const approve = await screen.findByRole("button", { name: "Approve action" });
  await user.click(approve);
  expect(screen.getByRole("alertdialog", { name: "Confirm approval" })).toBeInTheDocument();

  expect(await screen.findByText("EXPIRED", {}, { timeout: 2_000 })).toBeInTheDocument();
  expect(
    screen.queryByRole("alertdialog", { name: "Confirm approval" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Approve action" })).not.toBeInTheDocument();
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Close action review" })).toHaveFocus(),
  );
});

test("missing approval readiness metadata fails closed", async () => {
  const withoutReadiness = { ...action } as Partial<ActionRequestDetail>;
  delete withoutReadiness.can_approve;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(JSON.stringify(withoutReadiness), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <ActionReviewDialog actionId="action-1" onClose={vi.fn()} />
    </QueryClientProvider>,
  );

  await screen.findByRole("dialog", { name: "Review action request" });
  expect(screen.queryByRole("button", { name: "Approve action" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Reject plan" })).not.toBeInTheDocument();
  expect(
    await screen.findByText(/not currently approvable/i),
  ).toBeInTheDocument();
});

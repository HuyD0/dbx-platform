import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe, toHaveNoViolations } from "jest-axe";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { buildActionTimeline } from "../components/MissionDecisionDetail";
import { AssistantPanelProvider } from "../lib/assistant-panel";
import { ChatProvider } from "../lib/chat";
import { dateTime } from "../lib/format";
import type {
  ActionRequestDetail,
  DecisionQueueItem,
  Envelope,
  MissionControlData,
} from "../lib/types";
import { MissionControl, type MissionControlProps } from "./MissionControl";

expect.extend(toHaveNoViolations);

afterEach(() => {
  vi.unstubAllGlobals();
});

const now = new Date();
const evaluatedAt = now.toISOString();
const createdAt = new Date(now.getTime() - 4 * 60_000).toISOString();
const expiresAt = new Date(now.getTime() + 11 * 60_000).toISOString();

const queueItem: DecisionQueueItem = {
  action_id: "action-policy-1",
  action_type: "policy-sync",
  status: "AWAITING_APPROVAL",
  raw_status: "AWAITING_APPROVAL",
  effective_status: "AWAITING_APPROVAL",
  risk: "high",
  target_count: 2,
  proposer_id: "masked-proposer",
  proposer_email: "platform-owner@example.test",
  created_at: createdAt,
  expires_at: expiresAt,
  can_approve: true,
  impact: { changed_resource_count: 2, summary: "Restore managed constraints." },
  evidence_summary: {
    matched_count: 1,
    pillars: ["RISK"],
    freshest_at: evaluatedAt,
    coverage_status: "MATCHED",
  },
};

const populatedMission: Envelope<MissionControlData> = {
  data: {
    scope: {
      workspace: "workspace-1",
      workspace_name: "Production analytics",
      environment: "production",
      region: "canadacentral",
    },
    pending_approvals: 1,
    decision_queue: {
      evaluated_at: evaluatedAt,
      ranking: "risk-expiry-created-v1",
      active_count: 1,
      expiring_soon_count: 0,
      expired_count: 2,
      items: [queueItem],
    },
    outcomes: {
      cost: { open_findings: 0, value: 0, status: "unknown" },
      security: { open_findings: 1, value: 1, status: "attention" },
      risk: { open_findings: 1, value: 1, status: "attention" },
      performance: { open_findings: 0, value: 0, status: "unknown" },
    },
    decisions: [],
    changes: [],
    findings: {
      data: {
        run_ts: evaluatedAt,
        total: 2,
        by_area: { security: 1, risk: 1 },
        by_action: { "policy-sync": 1 },
      },
    },
    data_health: [
      {
        source: "Platform findings",
        status: "healthy",
        freshness: evaluatedAt,
      },
      {
        source: "Approval ledger",
        status: "degraded",
        freshness: evaluatedAt,
        notes: "Verification reader is catching up.",
      },
    ],
  },
  count: null,
  as_of: evaluatedAt,
  cached: false,
};

const detail: ActionRequestDetail = {
  ...queueItem,
  schema_version: 1,
  action_id: queueItem.action_id,
  action_type: queueItem.action_type,
  workspace_id: "workspace-1",
  environment: "production",
  status: "AWAITING_APPROVAL",
  raw_status: "AWAITING_APPROVAL",
  effective_status: "AWAITING_APPROVAL",
  risk: "high",
  parameters: {},
  preconditions: {},
  before_state: null,
  after_state: null,
  impact: queueItem.impact,
  rollback: { strategy: "restore exact before state" },
  verification: { method: "compare managed policy state" },
  created_at: createdAt,
  expires_at: expiresAt,
  idempotency_key: "idempotency-policy-1",
  evaluated_at: evaluatedAt,
  updated_at: evaluatedAt,
  terminal_reason: null,
  can_approve: true,
  plan_hash: "a".repeat(64),
  confirm_phrase: "approve policy-sync 2",
  actions_enabled: true,
  approver_required: true,
  targets: [
    { policy_id: "masked-policy-1", expected: "managed" },
    { policy_id: "masked-policy-2", expected: "managed" },
  ],
  plan_id: queueItem.action_id,
  action: queueItem.action_type,
  items: [
    { policy_id: "masked-policy-1", expected: "managed" },
    { policy_id: "masked-policy-2", expected: "managed" },
  ],
  summary: "Restore managed constraints.",
  evidence_correlation: {
    coverage_status: "MATCHED",
    total: 1,
    truncated: false,
    items: [
      {
        finding_id: "finding-1",
        check_name: "managed-policy-drift",
        pillar: "RISK",
        severity: "HIGH",
        confidence: 0.96,
        owner: "masked-owner",
        reason: "Managed policy differs from source.",
        state: "OPEN",
        freshness_at: evaluatedAt,
        proposed_action_type: "policy-sync",
        affected_resources: [{ resource_id: "masked-policy-1" }],
        match_type: "supports_action",
      },
    ],
  },
  approvals: [],
  events: [
    {
      event_id: "event-plan",
      action_id: queueItem.action_id,
      event_type: "PLAN_CREATED",
      from_status: null,
      actor_id: "masked-proposer",
      event_ts: createdAt,
      to_status: "AWAITING_APPROVAL",
      details: {},
    },
    {
      event_id: "event-unmapped",
      action_id: queueItem.action_id,
      event_type: "COMMENT_RECORDED",
      from_status: null,
      to_status: null,
      actor_id: "masked-proposer",
      event_ts: createdAt,
      details: {},
    },
  ],
};

function response(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function setNarrowViewport() {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
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

function setWideViewport() {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: query === "(min-width: 1280px)",
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

function renderMission(props: MissionControlProps = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const onOpen = vi.fn();
  const rendered = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={["/"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <ChatProvider>
          <AssistantPanelProvider onOpen={onOpen}>
            <main>
              <MissionControl {...props} />
            </main>
          </AssistantPanelProvider>
        </ChatProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...rendered, onOpen };
}

function stubPopulatedApi() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.startsWith("/api/mission-control")) return response(populatedMission);
    if (url === `/api/action-requests/${queueItem.action_id}`) return response(detail);
    if (url === "/api/chat") {
      return response({
        message: "Focused explanation.",
        proposals: [],
        citations: [],
        endpoint: "test",
      });
    }
    return response({ error: "not_found", message: `No fixture for ${url}` }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

test("renders the approval queue first and opens an accessible evidence sheet", async () => {
  setNarrowViewport();
  stubPopulatedApi();
  const user = userEvent.setup();
  const onAskDecision = vi.fn();
  const rendered = renderMission({ onAskDecision });

  expect(
    await screen.findByRole("heading", { name: "Decisions requiring you." }),
  ).toBeInTheDocument();
  const queueHeading = screen.getByRole("heading", { name: "Approval queue" });
  const postureHeading = screen.getByRole("heading", { name: "Operational posture" });
  expect(
    queueHeading.compareDocumentPosition(postureHeading) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(screen.getByText("2 exact targets")).toBeInTheDocument();
  expect(screen.getByText("1 current evidence match")).toBeInTheDocument();
  expect(screen.getByText(/Source coverage is partial/)).toBeInTheDocument();

  const trigger = screen.getByRole("button", {
    name: "Open Synchronize managed policies decision",
  });
  await user.click(trigger);
  const sheet = await screen.findByRole("dialog", {
    name: "Synchronize managed policies",
  });
  const close = within(sheet).getByRole("button", { name: "Close decision details" });
  await waitFor(() => expect(close).toHaveFocus());
  expect(within(sheet).getByText(dateTime(expiresAt))).toBeInTheDocument();
  expect(within(sheet).queryByText("just now")).not.toBeInTheDocument();
  expect(within(sheet).getByText("Changed resource count")).toBeInTheDocument();
  expect(
    within(sheet).getByText(/Summary first; expand the raw JSON/i),
  ).toBeInTheDocument();
  expect(within(sheet).queryByText(/"changed_resource_count"/)).not.toBeInTheDocument();
  await user.click(within(sheet).getByRole("button", { name: /Show JSON/ }));
  expect(within(sheet).getByText(/"changed_resource_count"/)).toBeInTheDocument();

  await user.click(within(sheet).getByRole("tab", { name: /^Evidence/ }));
  expect(
    await within(sheet).findByRole("heading", { name: "managed-policy-drift" }),
  ).toBeInTheDocument();
  expect(within(sheet).getByText("supports action")).toBeInTheDocument();
  expect(within(sheet).getByText("MATCHED coverage")).toBeInTheDocument();

  await user.click(within(sheet).getByRole("tab", { name: /^History/ }));
  expect(within(sheet).getByText("Plan created")).toBeInTheDocument();
  expect(within(sheet).queryByText("Comment recorded")).not.toBeInTheDocument();
  expect(await axe(document.body)).toHaveNoViolations();

  await user.keyboard("{Escape}");
  expect(
    screen.queryByRole("dialog", { name: "Synchronize managed policies" }),
  ).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();

  await user.click(trigger);
  const reopened = await screen.findByRole("dialog", {
    name: "Synchronize managed policies",
  });
  await user.click(within(reopened).getByRole("button", { name: /Ask agent/ }));
  expect(onAskDecision).toHaveBeenCalledWith(expect.objectContaining({ action_id: queueItem.action_id }));
  expect(
    screen.queryByRole("dialog", { name: "Synchronize managed policies" }),
  ).not.toBeInTheDocument();

  expect(await axe(rendered.container)).toHaveNoViolations();
});

test("closes the responsive sheet before opening exact-plan review", async () => {
  setNarrowViewport();
  stubPopulatedApi();
  const user = userEvent.setup();
  renderMission();

  const trigger = await screen.findByRole("button", {
    name: "Open Synchronize managed policies decision",
  });
  await user.click(trigger);
  const sheet = await screen.findByRole("dialog", {
    name: "Synchronize managed policies",
  });
  await user.click(within(sheet).getByRole("button", { name: "Review exact plan" }));

  expect(
    screen.queryByRole("dialog", { name: "Synchronize managed policies" }),
  ).not.toBeInTheDocument();
  expect(
    await screen.findByRole("dialog", { name: "Review action request" }),
  ).toBeInTheDocument();
});

test("opens the contextual assistant with the selected action resolved as focus", async () => {
  setNarrowViewport();
  const fetchMock = stubPopulatedApi();
  const user = userEvent.setup();
  const { onOpen } = renderMission();

  await user.click(
    await screen.findByRole("button", {
      name: "Open Synchronize managed policies decision",
    }),
  );
  const sheet = await screen.findByRole("dialog", {
    name: "Synchronize managed policies",
  });
  await user.click(within(sheet).getByRole("button", { name: /Ask agent/ }));

  await waitFor(() => {
    const chatCall = fetchMock.mock.calls.find(
      ([input]) => String(input) === "/api/chat",
    );
    expect(chatCall).toBeDefined();
    const body = JSON.parse(String(chatCall?.[1]?.body));
    expect(body.context.focus_action_id).toBe(queueItem.action_id);
  });
  expect(onOpen).toHaveBeenCalledOnce();
  expect(
    screen.queryByRole("dialog", { name: "Synchronize managed policies" }),
  ).not.toBeInTheDocument();
});

test("shows the selected decision inline at wide desktop widths", async () => {
  setWideViewport();
  stubPopulatedApi();
  renderMission();

  const detailRegion = await screen.findByRole("region", {
    name: "Synchronize managed policies",
  });
  expect(
    within(detailRegion).getByRole("button", { name: "Review exact plan" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("dialog", { name: "Synchronize managed policies" }),
  ).not.toBeInTheDocument();
});

test("manual refresh bypasses the Mission Control cache", async () => {
  setNarrowViewport();
  let completeRefresh: ((value: Response) => void) | undefined;
  const refreshResponse = new Promise<Response>((resolve) => {
    completeRefresh = resolve;
  });
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/mission-control?refresh=true") return refreshResponse;
    if (url === "/api/mission-control") return Promise.resolve(response(populatedMission));
    return Promise.resolve(
      response({ error: "not_found", message: `No fixture for ${url}` }, 404),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderMission();

  await screen.findByRole("heading", { name: "Decisions requiring you." });
  await user.click(screen.getByRole("button", { name: "Refresh" }));
  expect(screen.getByText("Refreshing data.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Refresh" })).toBeDisabled();
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(
        ([input]) => String(input) === "/api/mission-control?refresh=true",
      ),
    ).toBe(true),
  );
  completeRefresh?.(response(populatedMission));
  await waitFor(() =>
    expect(screen.getByText("Refresh complete.")).toBeInTheDocument(),
  );
});

test("does not replace a failed detail read with false zero-evidence or zero-history claims", async () => {
  setNarrowViewport();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/mission-control") return response(populatedMission);
      if (url === `/api/action-requests/${queueItem.action_id}`) {
        return response(
          {
            error: "control_plane_unavailable",
            message: "Action detail storage cannot be read.",
          },
          503,
        );
      }
      return response({ error: "not_found", message: `No fixture for ${url}` }, 404);
    }),
  );
  const user = userEvent.setup();
  renderMission();

  await user.click(
    await screen.findByRole("button", {
      name: "Open Synchronize managed policies decision",
    }),
  );
  const sheet = await screen.findByRole("dialog", {
    name: "Synchronize managed policies",
  });
  await user.click(within(sheet).getByRole("tab", { name: /^Evidence/ }));
  expect(
    await within(sheet).findByText("Action detail storage cannot be read."),
  ).toBeInTheDocument();
  expect(
    within(sheet).queryByText(/No current evidence was correlated/i),
  ).not.toBeInTheDocument();

  await user.click(within(sheet).getByRole("tab", { name: /^History/ }));
  expect(
    within(sheet).getByText("Action detail storage cannot be read."),
  ).toBeInTheDocument();
  expect(
    within(sheet).queryByText(/No recorded approval, execution, verification/i),
  ).not.toBeInTheDocument();
});

test("uses cautious language for a degraded zero state", async () => {
  setNarrowViewport();
  const zeroMission: Envelope<MissionControlData> = {
    data: {
      decision_queue: {
        evaluated_at: evaluatedAt,
        ranking: "risk-expiry-created-v1",
        active_count: 0,
        expiring_soon_count: 0,
        expired_count: 3,
        items: [],
      },
      outcomes: {},
      decisions: [],
      changes: [],
      findings: {
        data: {
          run_ts: evaluatedAt,
          total: 0,
          by_area: {},
          by_action: {},
        },
      },
      data_health: [
        {
          source: "Platform findings",
          status: "unavailable",
          notes: "Collector is offline.",
        },
      ],
    },
    count: null,
    as_of: evaluatedAt,
    cached: false,
  };
  vi.stubGlobal("fetch", vi.fn(async () => response(zeroMission)));
  const rendered = renderMission();

  expect(
    await screen.findByRole("heading", { name: "No open findings recorded." }),
  ).toBeInTheDocument();
  expect(screen.getByText("No approval request is waiting.")).toBeInTheDocument();
  expect(screen.getByText(/not that every possible check passed/i)).toBeInTheDocument();
  expect(screen.getByText("3")).toBeInTheDocument();
  expect(screen.getByText(/Source coverage is partial/)).toBeInTheDocument();
  expect(screen.queryByText(/all reporting sources are healthy/i)).not.toBeInTheDocument();
  expect(screen.getByText(/restore incomplete source coverage/i)).toBeInTheDocument();
  expect(await axe(rendered.container)).toHaveNoViolations();
});

test("does not tell operators to restore coverage when every reported source is reachable", async () => {
  setNarrowViewport();
  const completeZero: Envelope<MissionControlData> = {
    data: {
      decision_queue: {
        evaluated_at: evaluatedAt,
        ranking: "risk-expiry-created-v1",
        active_count: 0,
        expiring_soon_count: 0,
        expired_count: 0,
        items: [],
      },
      outcomes: {},
      decisions: [],
      changes: [],
      findings: {
        data: {
          run_ts: evaluatedAt,
          total: 0,
          by_area: {},
          by_action: {},
        },
      },
      data_health: [
        {
          source: "Platform findings",
          status: "healthy",
          freshness: evaluatedAt,
        },
        {
          source: "Approval ledger",
          status: "healthy",
          freshness: evaluatedAt,
        },
      ],
    },
    count: null,
    as_of: evaluatedAt,
    cached: false,
  };
  vi.stubGlobal("fetch", vi.fn(async () => response(completeZero)));
  renderMission();

  await screen.findByRole("heading", { name: "No open findings recorded." });
  expect(screen.getByText(/wait for the next normalized collection/i)).toBeInTheDocument();
  expect(screen.queryByText(/restore incomplete source coverage/i)).not.toBeInTheDocument();
});

test("keeps ranked findings in a separate compatibility path", async () => {
  setNarrowViewport();
  const findingsOnly = {
    scope: { workspace: "legacy-workspace", environment: "production" },
    outcomes: {
      open_findings: 1,
      by_pillar: { risk: 1 },
      by_severity: { HIGH: 1 },
      awaiting_approval: 0,
    },
    top_decisions: [
      {
        finding_id: "finding-legacy",
        proposed_action_type: "policy-sync",
        pillar: "RISK",
        severity: "HIGH",
        reason: "Managed controls differ.",
        affected_resources: [{ resource_id: "masked-policy" }],
      },
    ],
    data_health: {
      legacy_findings: {
        status: "healthy",
        freshness: evaluatedAt,
      },
    },
  };
  vi.stubGlobal("fetch", vi.fn(async () => response(findingsOnly)));
  renderMission();

  expect(
    await screen.findByRole("heading", { name: "Open evidence needs review." }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "Synchronize managed policies" }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("list", { name: "Approval queue" })).not.toBeInTheDocument();
  expect(screen.getByRole("list", { name: "Ranked open evidence" })).toBeInTheDocument();
  expect(screen.getByText("compatibility mode")).toBeInTheDocument();
});

test("renders loading and fail-closed error states", async () => {
  setNarrowViewport();
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => undefined)));
  const loading = renderMission();
  expect(
    screen.getByRole("heading", { name: "Loading decision records…" }),
  ).toBeInTheDocument();
  loading.unmount();

  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      response(
        {
          error: "control_plane_unavailable",
          message: "Decision storage cannot be read.",
        },
        503,
      ),
    ),
  );
  renderMission();
  expect(
    await screen.findByRole("heading", { name: "Decision records are unavailable." }),
  ).toBeInTheDocument();
  expect(screen.getByText("Decision storage cannot be read.")).toBeInTheDocument();
});

test("excludes expired plans from approval work and reports them cautiously", async () => {
  setNarrowViewport();
  const expiredMission: Envelope<MissionControlData> = {
    data: {
      decision_queue: {
        evaluated_at: evaluatedAt,
        ranking: "risk-expiry-created-v1",
        active_count: 0,
        expiring_soon_count: 0,
        expired_count: 0,
        items: [
          {
            ...queueItem,
            status: "AWAITING_APPROVAL",
            raw_status: "AWAITING_APPROVAL",
            effective_status: "EXPIRED",
            can_approve: false,
            expires_at: new Date(now.getTime() - 60_000).toISOString(),
          },
        ],
      },
      findings: {
        data: {
          run_ts: evaluatedAt,
          total: 0,
          by_area: {},
          by_action: {},
        },
      },
      decisions: [],
      changes: [],
      outcomes: {},
      data_health: [
        {
          source: "Approval ledger",
          status: "healthy",
          freshness: evaluatedAt,
        },
      ],
    },
    count: null,
    as_of: evaluatedAt,
    cached: false,
  };
  vi.stubGlobal("fetch", vi.fn(async () => response(expiredMission)));
  const rendered = renderMission();

  expect(
    await screen.findByRole("heading", { name: "No open findings recorded." }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", {
      name: "Open Synchronize managed policies decision",
    }),
  ).not.toBeInTheDocument();
  expect(screen.getByText("No approval request is waiting.")).toBeInTheDocument();
  expect(screen.getByText("1")).toBeInTheDocument();
  expect(await axe(rendered.container)).toHaveNoViolations();
});

test("labels proposal-only decision details without exposing approval", async () => {
  setNarrowViewport();
  const proposalMission: Envelope<MissionControlData> = {
    ...populatedMission,
    data: {
      ...populatedMission.data,
      decision_queue: {
        ...populatedMission.data.decision_queue!,
        items: [{ ...queueItem, can_approve: false }],
      },
      data_health: [
        {
          source: "Approval ledger",
          status: "proposal_only",
          freshness: evaluatedAt,
          notes: "Local plans cannot execute.",
        },
      ],
    },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/mission-control")) return response(proposalMission);
      if (url === `/api/action-requests/${queueItem.action_id}`) {
        return response({ ...detail, can_approve: false, actions_enabled: false });
      }
      return response({ error: "not_found", message: `No fixture for ${url}` }, 404);
    }),
  );
  const user = userEvent.setup();
  const rendered = renderMission();

  expect((await screen.findAllByText("proposal only")).length).toBeGreaterThan(0);
  expect(screen.getByText("review only")).toBeInTheDocument();
  await user.click(
    screen.getByRole("button", {
      name: "Open Synchronize managed policies decision",
    }),
  );
  const sheet = await screen.findByRole("dialog", {
    name: "Synchronize managed policies",
  });
  expect(
    await within(sheet).findByText(/This deployment is proposal-only/i),
  ).toBeInTheDocument();
  expect(await axe(rendered.container)).toHaveNoViolations();
});

test("timeline contains only lifecycle stages backed by recorded rows", () => {
  const succeeded: ActionRequestDetail = {
    ...detail,
    status: "SUCCEEDED",
    effective_status: "SUCCEEDED",
    can_approve: false,
    approvals: [
      {
        approval_id: "approval-1",
        action_id: queueItem.action_id,
        plan_hash: detail.plan_hash,
        decision: "APPROVED",
        approver_id: "masked-approver",
        approver_email: "approver@example.test",
        approver_role: "approver",
        confirmation: detail.confirm_phrase,
        decided_at: new Date(now.getTime() - 3 * 60_000).toISOString(),
      },
    ],
    events: [
      {
        event_id: "execute-1",
        action_id: queueItem.action_id,
        event_type: "EXECUTOR_SUBMITTED",
        from_status: "APPROVED",
        to_status: "EXECUTING",
        actor_id: "masked-executor",
        event_ts: new Date(now.getTime() - 2 * 60_000).toISOString(),
        details: {},
      },
      {
        event_id: "verify-1",
        action_id: queueItem.action_id,
        event_type: "VERIFICATION_COMPLETED",
        from_status: "VERIFYING",
        to_status: "SUCCEEDED",
        actor_id: "masked-executor",
        event_ts: new Date(now.getTime() - 60_000).toISOString(),
        details: {},
      },
      {
        event_id: "unknown-1",
        action_id: queueItem.action_id,
        event_type: "NOTE_ADDED",
        from_status: null,
        to_status: null,
        actor_id: "masked-operator",
        event_ts: now.toISOString(),
        details: {},
      },
    ],
  };

  const timeline = buildActionTimeline(succeeded);
  expect(timeline.map((entry) => entry.stage)).toEqual([
    "approval",
    "execution",
    "verification",
  ]);
  expect(timeline.some((entry) => entry.label === "Note added")).toBe(false);
});

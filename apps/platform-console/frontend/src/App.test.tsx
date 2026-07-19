import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { expect, test, vi } from "vitest";
import App from "./App";

function renderApp(initialEntry = "/") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={[initialEntry]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("global assistant launcher does not cover Mission Control decision actions", () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(
        JSON.stringify({
          error: "dependency_unavailable",
          message: "Test source unavailable.",
        }),
        { status: 503, headers: { "Content-Type": "application/json" } },
      ),
    ),
  );
  const mission = renderApp("/");
  expect(screen.queryByRole("button", { name: "Ask agent" })).not.toBeInTheDocument();
  mission.unmount();

  renderApp("/actions");
  expect(screen.getByRole("button", { name: "Ask agent" })).toBeInTheDocument();
});

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

test("Mission Control turns ranked evidence into governed decisions", async () => {
  const user = userEvent.setup();
  const mission = {
    data: {
      scope: {
        workspace: "enterprise-prod",
        workspace_name: "enterprise-prod",
        environment: "production",
        region: "canadacentral",
      },
      outcomes: {
        cost: {
          value: "$18.4K",
          open_findings: 6,
          status: "attention",
          summary: "Idle compute and failed-run waste dominate.",
        },
        security: {
          value: "1 serious",
          open_findings: 4,
          critical_findings: 1,
          status: "critical",
          summary: "Privileged grants need owner review.",
        },
        risk: {
          value: "policy drift",
          open_findings: 3,
          status: "attention",
          summary: "A managed cluster policy differs from source.",
        },
        performance: {
          value: "p95 +21%",
          open_findings: 5,
          status: "attention",
          summary: "A nightly feature job regressed after scaling.",
        },
      },
      pending_approvals: 2,
      decisions: [
        {
          finding_id: "finding-1",
          pillar: "RISK",
          severity: "HIGH",
          check_name: "managed-policy-drift",
          reason: "Three managed constraints differ from the repository policy.",
          proposed_action_type: "policy-sync",
          affected_resources: [
            { resource_id: "policy-1" },
            { resource_id: "policy-2" },
            { resource_id: "policy-3" },
          ],
          evidence: { source: "policy repository", fields_drifted: 3 },
          freshness_at: "2026-07-18T12:00:00Z",
        },
      ],
      data_health: [
        {
          source: "Platform findings",
          status: "healthy",
          freshness: "2026-07-18T12:00:00Z",
        },
        {
          source: "Approval ledger",
          status: "healthy",
          freshness: "2026-07-18T12:00:00Z",
        },
        {
          source: "Runtime inventory",
          status: "degraded",
          freshness: "2026-07-18T11:00:00Z",
        },
      ],
      findings: {
        data: {
          run_ts: "2026-07-18T12:00:00Z",
          total: 18,
          by_area: { cost: 6, security: 4, risk: 3, performance: 5 },
          by_action: { "policy-sync": 1 },
        },
      },
      changes: [],
    },
    count: null,
    as_of: "2026-07-18T12:00:00Z",
    cached: false,
  };
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/health")) {
      return new Response(
        JSON.stringify({
          status: "ok",
          version: "test",
          environment: "production",
          actions_enabled: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (url.startsWith("/api/mission-control")) {
      return new Response(JSON.stringify(mission), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url === "/api/chat") {
      return new Response(
        JSON.stringify({
          message: "Policy drift is ranked first because it weakens three managed controls.",
          proposals: [],
          endpoint: "test-agent",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (url === "/api/action-requests/plan") {
      return new Response(
        JSON.stringify({
          plan_id: "plan-1",
          action: "policy-sync",
          expires_at: Date.now() + 15 * 60 * 1000,
          items: [{ policy_id: "policy-1", change: "restore managed constraints" }],
          summary: { updated: 1 },
          confirm_phrase: "apply policy-sync 1",
          actions_enabled: false,
          plan_hash: "a".repeat(64),
          risk: "medium",
          impact: { changed_resource_count: 1 },
          rollback: { strategy: "restore exact before state" },
          verification: { exact_policy_match: true },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    return new Response(
      JSON.stringify({ error: "not_found", message: `No test response for ${url}` }),
      { status: 404, headers: { "Content-Type": "application/json" } },
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  renderApp();

  expect(
    await screen.findByRole("heading", { name: "Operational posture" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Cost" })).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "Synchronize managed policies" }),
  ).toBeInTheDocument();
  expect(screen.getByText("3 affected resources")).toBeInTheDocument();
  expect(screen.getByText("Exact plan required")).toBeInTheDocument();
  expect(screen.getByText("2 / 3")).toBeInTheDocument();

  const ask = screen.getByRole("button", { name: "Ask why this matters" });
  await user.click(ask);
  const investigator = await screen.findByRole("dialog", {
    name: "Read-only investigator",
  });
  expect(
    await within(investigator).findByText(
      "Policy drift is ranked first because it weakens three managed controls.",
    ),
  ).toBeInTheDocument();
  expect(
    fetchMock.mock.calls.some(([input]) => String(input) === "/api/chat"),
  ).toBe(true);

  await user.click(screen.getByRole("button", { name: "Close assistant" }));
  await waitFor(() => expect(ask).toHaveFocus());

  const review = screen.getByRole("button", { name: "Review exact plan" });
  await user.click(review);
  const planDialog = await screen.findByRole("dialog", { name: "Review exact plan" });
  expect(
    within(planDialog).getByText("This applies the reviewed action to 1 exact target."),
  ).toBeInTheDocument();
  expect(
    within(planDialog).getByRole("button", { name: "Why does this approval expire?" }),
  ).toBeInTheDocument();
  await user.click(within(planDialog).getByText("Technical details"));
  expect(within(planDialog).getByText("a".repeat(64))).toBeInTheDocument();
  expect(
    within(planDialog).getByText(
      "This deployment is proposal-only. The plan can be inspected and exported, but execution remains disabled until the audited executor and approver group are configured.",
    ),
  ).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Close approval dialog" }));
  await waitFor(() => expect(review).toHaveFocus());
});

test("five-jobs navigation exposes governance homes and the workspace scope", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).startsWith("/api/health")) {
        return new Response(
          JSON.stringify({
            status: "ok",
            version: "test",
            environment: "prod",
            actions_enabled: false,
            workspace_id: "7405609799238491",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(
        JSON.stringify({ error: "dependency_unavailable", message: "Test source unavailable." }),
        { status: 503, headers: { "Content-Type": "application/json" } },
      );
    }),
  );
  renderApp("/");

  const nav = screen.getByRole("navigation", { name: "Primary" });
  for (const label of [
    "Cost",
    "Data Governance",
    "AI Governance",
    "Risk",
    "Operations",
    "Learn",
  ]) {
    expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
  }
  expect(await screen.findByText("7405609799238491")).toBeInTheDocument();
});

test("legacy governance and security URLs land on the split governance pages", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(
        JSON.stringify({ error: "dependency_unavailable", message: "Test source unavailable." }),
        { status: 503, headers: { "Content-Type": "application/json" } },
      ),
    ),
  );
  const governance = renderApp("/governance");
  expect(
    await governance.findByRole("heading", { name: "Data Governance" }),
  ).toBeInTheDocument();
  governance.unmount();

  const security = renderApp("/security?tab=governance");
  expect(
    await security.findByRole("heading", { name: "Data Governance" }),
  ).toBeInTheDocument();
  security.unmount();

  const risk = renderApp("/security?tab=identity");
  expect(await risk.findByRole("heading", { name: "Risk" })).toBeInTheDocument();
  risk.unmount();

  const aiMl = renderApp("/ai-ml");
  expect(await aiMl.findByRole("heading", { name: "AI Governance" })).toBeInTheDocument();
});

import AxeBuilder from "@axe-core/playwright";
import {
  expect,
  test,
  type Locator,
  type Page,
  type Route,
  type TestInfo,
} from "@playwright/test";
import type {
  ActionRequestDetail,
  DecisionQueueItem,
  Envelope,
  MissionControlData,
} from "../src/lib/types";

type Theme = "light" | "dark";
type MissionState = "populated" | "zero";

const FIXED_NOW = new Date("2026-07-18T12:00:00.000Z");
const CREATED_AT = "2026-07-18T11:56:00.000Z";
const EXPIRES_AT = "2026-07-18T12:11:00.000Z";
const ACTION_ID = "action-policy-1";

const queueItem = {
  action_id: ACTION_ID,
  action_type: "policy-sync",
  status: "AWAITING_APPROVAL",
  raw_status: "AWAITING_APPROVAL",
  effective_status: "AWAITING_APPROVAL",
  risk: "high",
  target_count: 2,
  proposer_id: "masked-proposer",
  proposer_email: "platform-owner@example.test",
  created_at: CREATED_AT,
  expires_at: EXPIRES_AT,
  can_approve: false,
  impact: {
    changed_resource_count: 2,
    summary: "Restore managed constraints.",
  },
  evidence_summary: {
    matched_count: 1,
    pillars: ["RISK"],
    freshest_at: FIXED_NOW.toISOString(),
    coverage_status: "MATCHED",
  },
} satisfies DecisionQueueItem;

const populatedMission = {
  data: {
    scope: {
      workspace: "workspace-1",
      workspace_name: "Production analytics",
      environment: "production",
      region: "canadacentral",
    },
    pending_approvals: 1,
    decision_queue: {
      evaluated_at: FIXED_NOW.toISOString(),
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
        run_ts: FIXED_NOW.toISOString(),
        total: 2,
        by_area: { security: 1, risk: 1 },
        by_action: { "policy-sync": 1 },
      },
    },
    data_health: [
      {
        source: "Platform findings",
        status: "healthy",
        freshness: FIXED_NOW.toISOString(),
      },
      {
        source: "Approval ledger",
        status: "degraded",
        freshness: FIXED_NOW.toISOString(),
        notes: "Verification reader is catching up.",
      },
    ],
  },
  count: null,
  as_of: FIXED_NOW.toISOString(),
  cached: false,
} satisfies Envelope<MissionControlData>;

const zeroMission = {
  data: {
    scope: {
      workspace: "workspace-1",
      workspace_name: "Production analytics",
      environment: "production",
      region: "canadacentral",
    },
    decision_queue: {
      evaluated_at: FIXED_NOW.toISOString(),
      ranking: "risk-expiry-created-v1",
      active_count: 0,
      expiring_soon_count: 0,
      expired_count: 3,
      items: [],
    },
    outcomes: {
      cost: { open_findings: 0, value: 0, status: "unknown" },
      security: { open_findings: 0, value: 0, status: "unknown" },
      risk: { open_findings: 0, value: 0, status: "unknown" },
      performance: { open_findings: 0, value: 0, status: "unknown" },
    },
    decisions: [],
    changes: [],
    findings: {
      data: {
        run_ts: FIXED_NOW.toISOString(),
        total: 0,
        by_area: {},
        by_action: {},
      },
    },
    data_health: [
      {
        source: "Platform findings",
        status: "unavailable",
        freshness: null,
        notes: "Collector is offline.",
      },
    ],
  },
  count: null,
  as_of: FIXED_NOW.toISOString(),
  cached: false,
} satisfies Envelope<MissionControlData>;

const actionDetail = {
  ...queueItem,
  evaluated_at: FIXED_NOW.toISOString(),
  plan_hash: "a".repeat(64),
  actions_enabled: false,
  targets: [
    { policy_id: "masked-policy-1", expected: "managed" },
    { policy_id: "masked-policy-2", expected: "managed" },
  ],
  impact: {
    changed_resource_count: 2,
    summary: "Restore managed constraints.",
  },
  rollback: { strategy: "restore exact before state" },
  verification: { exact_policy_match: true },
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
        owner: null,
        reason: "Managed policy differs from source.",
        state: "OPEN",
        freshness_at: FIXED_NOW.toISOString(),
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
      action_id: ACTION_ID,
      event_type: "PLAN_CREATED",
      actor_id: "masked-proposer",
      event_ts: CREATED_AT,
      from_status: null,
      to_status: "AWAITING_APPROVAL",
      details: {},
    },
  ],
} satisfies Partial<ActionRequestDetail>;

function project(testInfo: TestInfo): { theme: Theme; width: number } {
  const metadata = testInfo.project.metadata as { theme?: unknown; width?: unknown };
  const theme = metadata.theme;
  const width = metadata.width;
  if ((theme !== "light" && theme !== "dark") || typeof width !== "number") {
    throw new Error(`Invalid Playwright project metadata: ${JSON.stringify(metadata)}`);
  }
  return { theme, width };
}

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function mockApi(page: Page, state: MissionState) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/health") {
      await json(route, {
        status: "ok",
        version: "e2e",
        environment: "production",
        actions_enabled: false,
      });
      return;
    }
    if (url.pathname === "/api/mission-control") {
      await json(route, state === "populated" ? populatedMission : zeroMission);
      return;
    }
    if (url.pathname === `/api/action-requests/${ACTION_ID}`) {
      await json(route, actionDetail);
      return;
    }
    if (url.pathname === "/api/estimator/patterns") {
      await json(route, {
        data: [
          {
            pattern: "doc_chat",
            label: "Chat with your documents",
            description: "Answers grounded in your own files.",
            example_prompt: "What does our travel policy say?",
            defaults: { needs_knowledge_base: true, needs_memory: false },
          },
          {
            pattern: "summarize",
            label: "Summarize long content",
            description: "Short, consistent summaries of long material.",
            example_prompt: "Summarize this report.",
            defaults: { needs_knowledge_base: false, needs_memory: false },
          },
        ],
        count: 2,
        as_of: new Date(FIXED_NOW).toISOString(),
        cached: false,
      });
      return;
    }
    await json(
      route,
      { error: "not_found", message: `No e2e fixture for ${url.pathname}` },
      404,
    );
  });
}

async function openMission(page: Page, testInfo: TestInfo, state: MissionState = "populated") {
  const { theme } = project(testInfo);
  await page.clock.setFixedTime(FIXED_NOW);
  await page.addInitScript((selectedTheme: Theme) => {
    localStorage.setItem("theme", selectedTheme);
  }, theme);
  await mockApi(page, state);
  await page.goto("/");
  await expect(
    page.getByRole("heading", {
      name: state === "populated" ? "Decisions requiring you." : "No open findings recorded.",
    }),
  ).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(() =>
        document.documentElement.classList.contains("dark") ? "dark" : "light",
      ),
    )
    .toBe(theme);
}

async function expectVisibleFocus(locator: Locator) {
  await expect(locator).toBeFocused();
  const focus = await locator.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return {
      inViewport:
        rect.width > 0 &&
        rect.height > 0 &&
        rect.right > 0 &&
        rect.bottom > 0 &&
        rect.left < innerWidth &&
        rect.top < innerHeight,
      unobscured: (() => {
        const hit = document.elementFromPoint(
          rect.left + rect.width / 2,
          rect.top + rect.height / 2,
        );
        return hit === element || (hit instanceof Node && element.contains(hit));
      })(),
      outlineStyle: style.outlineStyle,
      outlineWidth: Number.parseFloat(style.outlineWidth),
    };
  });
  expect(focus.inViewport).toBe(true);
  expect(focus.unobscured).toBe(true);
  expect(focus.outlineStyle).not.toBe("none");
  expect(focus.outlineWidth).toBeGreaterThanOrEqual(2);
}

async function tabTo(page: Page, target: Locator, maximumTabs = 32) {
  for (let index = 0; index < maximumTabs; index += 1) {
    await page.keyboard.press("Tab");
    if (await target.evaluate((element) => element === document.activeElement)) return;
  }
  const active = await page.evaluate(() => {
    const element = document.activeElement;
    return element instanceof HTMLElement
      ? element.getAttribute("aria-label") ?? element.innerText ?? element.tagName
      : "unknown";
  });
  throw new Error(`Could not reach target by keyboard after ${maximumTabs} tabs; focused ${active}`);
}

async function assertNoPageOverflow(page: Page) {
  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth + 1);
}

async function preserveDesktopVisualBaselineHeight(page: Page, width: number) {
  if (width !== 1440) return;
  // The checked-in desktop populated snapshots include a short blank page tail.
  // Keep that tail explicit so full-page screenshots remain stable when browser
  // layout engines round content height a few pixels differently.
  await page.addStyleTag({
    content: "html, body, #root { min-height: 1543px !important; }",
  });
}

async function assertControlTargets(page: Page) {
  const failures = await page.evaluate(() => {
    const controls = Array.from(
      document.querySelectorAll<HTMLElement>("button, [role='tab'], a[href]"),
    );
    return controls.flatMap((element) => {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      const visible =
        rect.width > 0 &&
        rect.height > 0 &&
        style.visibility !== "hidden" &&
        style.display !== "none" &&
        !element.closest("[aria-hidden='true']");
      if (!visible) return [];

      const name =
        element.getAttribute("aria-label") ??
        element.innerText.trim().replace(/\s+/g, " ").slice(0, 80) ??
        element.tagName;
      const issues: string[] = [];
      if (
        (element.matches("button") || element.getAttribute("role") === "tab") &&
        (rect.width < 23.5 || rect.height < 23.5)
      ) {
        issues.push(`${name}: ${rect.width.toFixed(1)}×${rect.height.toFixed(1)} (<24)`);
      }
      if (element.classList.contains("min-h-11") && rect.height < 43.5) {
        issues.push(`${name}: ${rect.height.toFixed(1)}px high (<44)`);
      }
      if (element.classList.contains("min-w-11") && rect.width < 43.5) {
        issues.push(`${name}: ${rect.width.toFixed(1)}px wide (<44)`);
      }
      return issues;
    });
  });
  expect(failures).toEqual([]);
}

test("responsive theme is reflow-safe and has no serious accessibility violations", async ({
  page,
}, testInfo) => {
  await openMission(page, testInfo);
  await assertNoPageOverflow(page);
  await assertControlTargets(page);

  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  const severe = results.violations
    .filter((violation) => ["serious", "critical"].includes(violation.impact ?? ""))
    .map((violation) => ({
      id: violation.id,
      impact: violation.impact,
      help: violation.help,
      targets: violation.nodes.map((node) => node.target),
    }));
  expect(severe).toEqual([]);
});

test("AI Cost Planner wizard renders and has no serious accessibility violations", async ({
  page,
}, testInfo) => {
  await openMission(page, testInfo);
  await page.goto("/cost-planner");
  await expect(page.getByRole("heading", { name: "What should it do?" })).toBeVisible();
  await expect(
    page.getByRole("radio", { name: /Chat with your documents/ }),
  ).toBeVisible();
  await assertNoPageOverflow(page);

  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  const severe = results.violations
    .filter((violation) => ["serious", "critical"].includes(violation.impact ?? ""))
    .map((violation) => ({
      id: violation.id,
      impact: violation.impact,
      help: violation.help,
      targets: violation.nodes.map((node) => node.target),
    }));
  expect(severe).toEqual([]);
});

test("keyboard opens and closes the responsive decision sheet with visible focus", async ({
  page,
}, testInfo) => {
  const { width } = project(testInfo);
  test.skip(width >= 1280, "Wide layouts expose the selected decision inline.");
  await openMission(page, testInfo);

  const trigger = page.getByRole("button", {
    name: "Open Synchronize managed policies decision",
  });
  await tabTo(page, trigger);
  await expectVisibleFocus(trigger);
  await page.keyboard.press("Enter");

  const sheet = page.getByRole("dialog", { name: "Synchronize managed policies" });
  await expect(sheet).toBeVisible();
  const close = sheet.getByRole("button", { name: "Close decision details" });
  await expectVisibleFocus(close);
  await assertNoPageOverflow(page);
  await assertControlTargets(page);
  const reviewPlan = sheet.getByRole("button", { name: "Review exact plan" });
  await tabTo(page, reviewPlan);
  await expectVisibleFocus(reviewPlan);
  const askAgent = sheet.getByRole("button", { name: "Ask agent" });
  await tabTo(page, askAgent);
  await expectVisibleFocus(askAgent);
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
  await expectVisibleFocus(trigger);
});

test("reduced-motion preference suppresses transitions and smooth scrolling", async ({
  page,
}, testInfo) => {
  const { theme, width } = project(testInfo);
  test.skip(width !== 375, "One mobile viewport per theme covers the global motion override.");
  await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
  await openMission(page, testInfo);

  const motion = await page.evaluate(() => {
    const visible = Array.from(document.querySelectorAll<HTMLElement>("*")).filter((element) => {
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    });
    const seconds = (value: string) =>
      value.split(",").map((part) => {
        const duration = part.trim();
        return duration.endsWith("ms")
          ? Number.parseFloat(duration) / 1_000
          : Number.parseFloat(duration);
      });
    return {
      scrollBehavior: getComputedStyle(document.documentElement).scrollBehavior,
      maximumTransitionSeconds: Math.max(
        0,
        ...visible.flatMap((element) => seconds(getComputedStyle(element).transitionDuration)),
      ),
      maximumAnimationSeconds: Math.max(
        0,
        ...visible.flatMap((element) => seconds(getComputedStyle(element).animationDuration)),
      ),
    };
  });
  expect(motion.scrollBehavior).toBe("auto");
  expect(motion.maximumTransitionSeconds).toBeLessThanOrEqual(0.001);
  expect(motion.maximumAnimationSeconds).toBeLessThanOrEqual(0.001);
});

test("768px layout reflows at the CSS-width equivalent of 200% zoom", async ({
  page,
}, testInfo) => {
  const { width } = project(testInfo);
  test.skip(width !== 768, "A 768→384 CSS-pixel viewport represents the 200% reflow case.");
  await openMission(page, testInfo);

  await page.setViewportSize({ width: 384, height: 900 });
  await expect(page.getByRole("heading", { name: "Decisions requiring you." })).toBeVisible();
  await assertNoPageOverflow(page);
  const headingBox = await page
    .getByRole("heading", { name: "Decisions requiring you." })
    .boundingBox();
  expect(headingBox).not.toBeNull();
  expect((headingBox?.x ?? 0) + (headingBox?.width ?? 0)).toBeLessThanOrEqual(384);
});

test("populated Mission Control visual baseline", async ({ page }, testInfo) => {
  const { width } = project(testInfo);
  test.skip(![375, 1440].includes(width), "Visual baselines use one mobile and one desktop width.");
  await openMission(page, testInfo);
  if (width === 1440) {
    await expect(
      page.getByRole("region", { name: "Synchronize managed policies" }),
    ).toBeVisible();
    await expect(page.getByText("masked-policy-1")).toBeVisible();
  }
  await preserveDesktopVisualBaselineHeight(page, width);
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  await expect(page).toHaveScreenshot("mission-populated.png", { fullPage: true });
});

test("cautious-zero Mission Control visual baseline", async ({ page }, testInfo) => {
  const { width } = project(testInfo);
  test.skip(![375, 1440].includes(width), "Visual baselines use one mobile and one desktop width.");
  await openMission(page, testInfo, "zero");
  await expect(page.getByText("No approval request is waiting.")).toBeVisible();
  await expect(page.getByText(/not that every possible check passed/i)).toBeVisible();
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  await expect(page).toHaveScreenshot("mission-cautious-zero.png", { fullPage: true });
});

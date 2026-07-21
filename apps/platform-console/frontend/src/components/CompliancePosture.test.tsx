import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { ComplianceRadarCard, ZdrEnforcer } from "./CompliancePosture";

const posture = {
  data: {
    metrics: [
      ["zdr", "ZDR Enforced Ratio", 50],
      ["content_safety", "Content Safety Mitigation", 100],
      ["access_control", "Access Control Consistency", 75],
      ["audit_logging", "Audit Logging", 80],
      ["rate_limit_headroom", "Rate Limit Headroom", 42],
    ].map(([id, label, value_pct]) => ({
      id,
      label,
      value_pct,
      compliant_resources: 1,
      evaluated_resources: 2,
      total_resources: 2,
      evidence_note: "Current attested evidence.",
    })),
    zdr_alerts: [
      {
        resource_id: "/subscriptions/sub/resourceGroups/rg/providers/ai/accounts/risky",
        resource_name: "risky-foundry",
        scope: "workspace",
        provider: "Microsoft Foundry",
        status: "disabled",
        remediation: "Move traffic to an attested ZDR deployment and revalidate.",
      },
    ],
    unverified_zdr_resources: 0,
    evaluated_resources: 2,
  },
  count: null,
  as_of: "2026-07-20T12:00:00Z",
  cached: false,
};

function renderCompliance() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ComplianceRadarCard />
      <ZdrEnforcer />
    </QueryClientProvider>,
  );
}

test("renders the five-axis posture and explicit ZDR remediation alert", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(JSON.stringify(posture), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  renderCompliance();

  expect(await screen.findByRole("img", { name: "AI compliance radar chart" })).toBeVisible();
  expect(screen.getByText("ZDR Enforced Ratio")).toBeVisible();
  expect(screen.getByText("50%")).toBeVisible();
  expect(screen.getByRole("alert")).toHaveTextContent("ZDR disabled · risky-foundry");
  expect(screen.getByRole("alert")).toHaveTextContent("Move traffic to an attested ZDR");
  expect(fetch).toHaveBeenCalledTimes(1);
});

test("keeps unknown ZDR coverage visible beside explicit disabled alerts", async () => {
  const mixedPosture = {
    ...posture,
    data: { ...posture.data, unverified_zdr_resources: 3 },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(JSON.stringify(mixedPosture), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );

  renderCompliance();

  expect(await screen.findByRole("alert")).toHaveTextContent("ZDR disabled · risky-foundry");
  expect(screen.getByText("ZDR requires evidence for 3 resources")).toBeVisible();
  expect(screen.getByText("Critical")).toHaveClass("text-status-critical");
});

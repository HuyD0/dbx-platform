import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import {
  aggregateGatewayTelemetry,
  classifyLiveRates,
  GatewayTelemetry,
  LiveRatesIndicator,
} from "./GatewayTelemetry";

test("classifies live, stale, empty, and ungoverned samples honestly", () => {
  const now = Date.parse("2026-07-20T12:00:00Z");
  const response = {
    data: [
      {
        usage_date: "2026-07-20",
        source: "system.ai_gateway.usage",
      },
    ],
    count: 1,
    as_of: "2026-07-20T11:59:00Z",
    cached: false,
  };

  expect(classifyLiveRates(response, now).kind).toBe("live");
  expect(classifyLiveRates({ ...response, as_of: "2026-07-20T11:40:00Z" }, now).kind).toBe(
    "stale",
  );
  expect(classifyLiveRates({ ...response, data: [], count: 0 }, now).kind).toBe("no-samples");
  expect(
    classifyLiveRates(
      { ...response, data: [{ usage_date: "2026-07-20", source: "other" }] },
      now,
    ).kind,
  ).toBe("unavailable");
});

test("aggregates gateway token volume and keeps the slowest daily p95", () => {
  expect(
    aggregateGatewayTelemetry([
      {
        usage_date: "2026-07-18",
        input_tokens: 100,
        output_tokens: 40,
        requests: 2,
        p95_latency_ms: 380,
      },
      {
        usage_date: "2026-07-18",
        input_tokens: 50,
        output_tokens: 10,
        requests: 1,
        p95_latency_ms: 420,
      },
      { usage_date: "invalid", input_tokens: 999 },
    ]),
  ).toEqual([
    {
      date: "2026-07-18",
      inputTokens: 150,
      outputTokens: 50,
      requests: 3,
      p95LatencyMs: 420,
    },
  ]);
});

test("renders accessible token and latency summaries from persisted samples", async () => {
  const now = new Date();
  const usageDate = now.toISOString().slice(0, 10);
  const fetchMock = vi.fn(async () =>
    new Response(
      JSON.stringify({
        data: [
          {
            usage_date: usageDate,
            input_tokens: 8_000,
            output_tokens: 2_000,
            requests: 20,
            p95_latency_ms: 480,
            source: "system.ai_gateway.usage",
          },
        ],
        count: 1,
        as_of: now.toISOString(),
        cached: false,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <LiveRatesIndicator days={30} />
      <GatewayTelemetry days={30} />
    </QueryClientProvider>,
  );

  expect(screen.getByText("◌ Checking rates")).toBeInTheDocument();
  expect(await screen.findByText("10K")).toBeInTheDocument();
  expect(screen.getByText("480")).toBeInTheDocument();
  const live = screen.getByText("● Live Rates");
  expect(live).toHaveClass("text-green-accent");
  expect(live).toHaveAccessibleName("Telemetry freshness: Live Rates");
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(
    screen.getByRole("figure", { name: "Daily AI Gateway input and output token throughput" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("figure", { name: "Daily AI Gateway p95 latency trend" }),
  ).toBeInTheDocument();
});

test("keeps both telemetry modules useful when the window has no traffic", async () => {
  const fetchMock = vi.fn(async () =>
    new Response(
      JSON.stringify({
        data: [],
        count: 0,
        as_of: new Date().toISOString(),
        cached: false,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <LiveRatesIndicator days={7} />
      <GatewayTelemetry days={7} />
    </QueryClientProvider>,
  );

  expect(await screen.findByText("○ No rate samples")).toBeInTheDocument();
  expect(
    await screen.findAllByText(/No AI Gateway telemetry was persisted in the last 7 days/),
  ).toHaveLength(2);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

test("shows an unavailable state when the governed telemetry request fails", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(
        JSON.stringify({ error: "dependency_unavailable", message: "source offline" }),
        { status: 503, headers: { "Content-Type": "application/json" } },
      ),
    ),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <LiveRatesIndicator />
    </QueryClientProvider>,
  );

  expect(await screen.findByText("× Rates unavailable")).toBeInTheDocument();
});

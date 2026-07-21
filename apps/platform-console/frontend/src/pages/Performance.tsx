import { useState } from "react";
import { FindingsSection } from "../components/FindingsSection";
import { GatewayTelemetry, LiveRatesIndicator } from "../components/GatewayTelemetry";
import { Card, SectionTitle } from "../components/ui";

const WINDOWS = [7, 30, 90];

export function Performance() {
  const [days, setDays] = useState(30);
  return (
    <div className="space-y-5">
      <Card>
        <SectionTitle
          title="Analysis window"
          subtitle="All regression and utilization views use the same comparison period"
          right={<LiveRatesIndicator />}
        />
        <div className="flex items-center gap-1" role="group" aria-label="Performance analysis window">
          {WINDOWS.map((window) => (
            <button
              key={window}
              type="button"
              onClick={() => setDays(window)}
              aria-pressed={days === window}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium ${
                days === window
                  ? "bg-accent text-white"
                  : "border border-grid text-ink-2 hover:bg-hairline"
              }`}
            >
              {window} days
            </button>
          ))}
        </div>
      </Card>

      <GatewayTelemetry days={days} />

      <div className="grid gap-4 xl:grid-cols-12">
        <div className="xl:col-span-7 [&>*]:h-full">
          <FindingsSection
            title="Job duration regressions"
            subtitle="p50/p95, queue time, retries and SLA exposure against the prior window"
            path="/api/performance/job-regressions"
            params={{ days }}
            emptyMessage="No material job regression."
          />
        </div>
        <div className="xl:col-span-5 [&>*]:h-full">
          <FindingsSection
            title="SQL query regressions"
            subtitle="Latency, queueing, bytes scanned and cost-per-query shifts"
            path="/api/performance/query-regressions"
            params={{ days }}
            emptyMessage="No material query regression."
          />
        </div>
        <div className="xl:col-span-4 [&>*]:h-full">
          <FindingsSection
            title="Under-utilized clusters"
            subtitle="Observed load does not justify size; recommendations preserve declared SLO headroom"
            path="/api/cost/cluster-utilization"
            params={{ days }}
            emptyMessage="No under-utilized clusters."
          />
        </div>
        <div className="xl:col-span-4 [&>*]:h-full">
          <FindingsSection
            title="SQL warehouse pressure"
            subtitle="Idle spend, queueing and sustained capacity pressure"
            path="/api/cost/warehouse-utilization"
            params={{ days }}
            emptyMessage="No mis-sized SQL warehouse."
          />
        </div>
        <div className="xl:col-span-4 [&>*]:h-full">
          <FindingsSection
            title="Failed and retry waste"
            subtitle="Cost and elapsed time burned on failed, timed-out or retried runs"
            path="/api/cost/failed-run-waste"
            params={{ days }}
            emptyMessage="No failed-run waste."
          />
        </div>
        <div className="xl:col-span-12 [&>*]:h-full">
          <FindingsSection
            title="Serving reliability"
            subtitle="p95 latency, error rate, retry amplification and cost per successful request"
            path="/api/performance/serving-slo"
            params={{ days }}
            emptyMessage="No model-serving SLO regression."
          />
        </div>
      </div>
    </div>
  );
}

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { BarList } from "../components/BarList";
import { DataTable } from "../components/DataTable";
import { FindingsSection } from "../components/FindingsSection";
import { apiGet } from "../lib/api";
import type { Envelope, Row } from "../lib/types";
import { Card, EmptyState, ErrorState, SectionTitle, Skeleton } from "../components/ui";

const WINDOWS = [7, 30, 90];

function ModelHygiene() {
  const query = useQuery({
    queryKey: ["/api/ml/model-hygiene"],
    queryFn: () =>
      apiGet<Envelope<{ findings: Row[]; truncated: boolean }>>("/api/ml/model-hygiene"),
    staleTime: 60_000,
    retry: false,
  });
  const findings = query.data?.data.findings ?? [];
  return (
    <Card>
      <SectionTitle
        title="Model registry hygiene"
        subtitle="Empty shells, no owner, stale, unaliased, never served"
      />
      {query.isPending ? (
        <Skeleton rows={3} />
      ) : query.isError ? (
        <ErrorState error={query.error} />
      ) : findings.length === 0 ? (
        <EmptyState message="Registry is clean." />
      ) : (
        <>
          <DataTable rows={findings} />
          {query.data.data.truncated && (
            <p className="mt-1 text-xs text-muted">Model listing was truncated.</p>
          )}
        </>
      )}
    </Card>
  );
}

export function AiMl() {
  const [days, setDays] = useState(30);
  return (
    <div className="space-y-4">
      <FindingsSection
        title="Serving endpoint audit"
        subtitle="Stuck endpoints, missing scale-to-zero, missing inference tables or AI Gateway config"
        path="/api/ml/endpoint-audit"
        emptyMessage="No serving endpoint findings."
      />
      <FindingsSection
        title="Stale endpoints"
        subtitle="No requests in the usage window"
        path="/api/ml/stale-endpoints"
        emptyMessage="No stale endpoints."
      />
      <ModelHygiene />
      <FindingsSection
        title="GPU cluster audit"
        subtitle="Interactive GPU clusters without autotermination or past the uptime threshold"
        path="/api/ml/gpu-audit"
        emptyMessage="No GPU findings."
      />
      <FindingsSection
        title="Vector search audit"
        subtitle="Endpoints billing with no indexes, or unhealthy"
        path="/api/ml/vector-search-audit"
        emptyMessage="No vector search findings."
      />

      <div className="flex items-center gap-1 text-xs">
        <span className="mr-1 text-muted">Spend window:</span>
        {WINDOWS.map((w) => (
          <button
            key={w}
            type="button"
            onClick={() => setDays(w)}
            aria-pressed={days === w}
            className={`rounded-lg px-2.5 py-1 font-medium ${
              days === w ? "bg-accent text-white" : "border border-grid text-ink-2 hover:bg-hairline"
            }`}
          >
            {w}d
          </button>
        ))}
      </div>
      <FindingsSection
        title="AI/ML spend"
        subtitle={`By product, SKU and endpoint, last ${days} days`}
        path="/api/ml/serving-cost"
        params={{ days }}
        emptyMessage="No AI/ML spend in the window."
        render={(rows) => (
          <BarList
            maxBars={12}
            data={rows.map((r) => ({
              label: [r.billing_origin_product, r.endpoint_name ?? r.sku_name]
                .filter(Boolean)
                .join(" · "),
              value: Number(r.list_cost_usd ?? 0),
            }))}
          />
        )}
      />
      <FindingsSection
        title="Token usage"
        subtitle={`Per endpoint and requester, last ${days} days`}
        path="/api/ml/token-usage"
        params={{ days }}
        emptyMessage="No token usage in the window."
      />
      <FindingsSection
        title="GPU spend share"
        subtitle={`GPU vs total classic-compute list cost, last ${days} days`}
        path="/api/ml/gpu-spend"
        params={{ days }}
        emptyMessage="No GPU spend in the window."
      />
    </div>
  );
}

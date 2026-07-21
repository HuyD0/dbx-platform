import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { apiGet, apiPost } from "../../lib/api";
import type { DeploymentLink, Envelope } from "../../lib/types";
import { usd } from "../../lib/format";
import { DataTable } from "../DataTable";
import { Badge, Card, EmptyState, SectionTitle } from "../ui";

const ANCHOR_KINDS = [
  { value: "azure_resource_group", label: "Azure resource group" },
  { value: "databricks_project_tag", label: "Databricks project tag" },
  { value: "databricks_team_tag", label: "Databricks team tag" },
];

const inputClass =
  "w-full rounded-lg border border-hairline bg-page px-3 py-1.5 text-sm text-ink " +
  "focus:outline-none focus-visible:ring-2 focus-visible:ring-series-1";

/** "I deployed this": link a saved estimate to the real cost anchor it runs
 * under (a resource group or project/team tag) plus the tier + scenario that
 * was actually shipped. That link is what lets the drift check compare this
 * projection against real spend — the projection itself is read server-side
 * from the estimate, never sent from here. */
export function LinkDeploymentForm({
  estimateId,
  tiers,
  scenarios,
}: {
  estimateId: string;
  tiers: string[];
  scenarios: string[];
}) {
  const [open, setOpen] = useState(false);
  const [tier, setTier] = useState(tiers[0] ?? "production");
  const [scenario, setScenario] = useState(scenarios[0] ?? "azure");
  const [anchorKind, setAnchorKind] = useState(ANCHOR_KINDS[0].value);
  const [anchorValue, setAnchorValue] = useState("");
  const queryClient = useQueryClient();

  const link = useMutation({
    mutationFn: () =>
      apiPost<{ deployment_id: string }>("/api/estimator/deployments/link", {
        estimate_id: estimateId,
        tier,
        scenario,
        anchor_kind: anchorKind,
        anchor_value: anchorValue,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["estimator", "deployments"] });
      setOpen(false);
      setAnchorValue("");
    },
  });

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="rounded-lg border border-hairline px-3 py-1.5 text-xs text-ink-2 hover:text-ink"
      >
        I deployed this
      </button>
    );
  }
  return (
    <div className="flex flex-wrap items-end gap-2 rounded-xl border border-hairline p-3">
      <label className="text-xs text-ink-2">
        Tier
        <select className={`${inputClass} mt-1`} value={tier} onChange={(e) => setTier(e.target.value)}>
          {tiers.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </label>
      <label className="text-xs text-ink-2">
        Runs on
        <select
          className={`${inputClass} mt-1`}
          value={scenario}
          onChange={(e) => setScenario(e.target.value)}
        >
          {scenarios.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
      <label className="text-xs text-ink-2">
        Cost anchor
        <select
          className={`${inputClass} mt-1`}
          value={anchorKind}
          onChange={(e) => setAnchorKind(e.target.value)}
        >
          {ANCHOR_KINDS.map((a) => (
            <option key={a.value} value={a.value}>
              {a.label}
            </option>
          ))}
        </select>
      </label>
      <label className="text-xs text-ink-2">
        {anchorKind === "azure_resource_group" ? "Resource group name" : "Tag value"}
        <input
          className={`${inputClass} mt-1`}
          value={anchorValue}
          onChange={(e) => setAnchorValue(e.target.value)}
          placeholder={anchorKind === "azure_resource_group" ? "rg-my-solution" : "my-project"}
        />
      </label>
      <button
        type="button"
        disabled={!anchorValue.trim() || link.isPending}
        onClick={() => link.mutate()}
        className="rounded-lg bg-series-1 px-3 py-1.5 text-xs font-semibold text-page disabled:opacity-50"
      >
        {link.isPending ? "Linking…" : "Link"}
      </button>
      <button
        type="button"
        onClick={() => setOpen(false)}
        className="rounded-lg border border-hairline px-3 py-1.5 text-xs text-ink-2"
      >
        Cancel
      </button>
      {link.isError && (
        <span role="alert" className="w-full text-xs text-danger">
          {(link.error as Error).message}
        </span>
      )}
    </div>
  );
}

/** Lists linked deployments so the drift check's inputs are visible. */
export function DeploymentsPanel() {
  const query = useQuery({
    queryKey: ["estimator", "deployments"],
    queryFn: () => apiGet<Envelope<DeploymentLink[]>>("/api/estimator/deployments"),
    staleTime: 60_000,
  });
  const rows = (query.data?.data ?? []).filter((row) => row.active);
  return (
    <Card>
      <SectionTitle
        title="Linked deployments"
        subtitle="Estimates tied to a real cost anchor — the drift check compares each against actual spend."
      />
      {rows.length === 0 ? (
        <EmptyState message="No deployments linked yet. Use “I deployed this” on a saved estimate to start tracking estimate vs. actual." />
      ) : (
        <DataTable
          rows={rows.map((row) => ({
            tier: row.tier,
            "runs on": row.scenario,
            anchor: `${row.anchor_kind}: ${row.anchor_value}`,
            "projected / month": usd(row.monthly_projected_usd),
            linked: row.created_at,
          }))}
          caption="Linked deployments"
          exportName="ai-cost-planner-deployments"
          pageSize={8}
        />
      )}
      {query.isError && (
        <Badge tone="warning">deployment list unavailable</Badge>
      )}
    </Card>
  );
}

import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { FindingsSection } from "../components/FindingsSection";
import { PageHeader, Tabs } from "../components/ui";
import { AiMl } from "./AiMl";

const AI_TABS = [
  { id: "inventory", label: "Inventory & access" },
  { id: "monitor", label: "Usage monitor" },
  { id: "serving", label: "Serving hygiene" },
];

const WINDOWS = [7, 30, 90];

function InventoryAccess() {
  return (
    <div className="space-y-4">
      <FindingsSection
        title="Model inventory"
        subtitle="Unity Catalog models, serving endpoints and Azure OpenAI/Foundry deployments in one register, with key-auth exposure flags"
        path="/api/ai-governance/catalog"
        emptyMessage="No AI inventory rows yet — run the scheduled ai-catalog sync job to populate this register."
      />
      <FindingsSection
        title="Access graph"
        subtitle="Who can invoke or administer each model, via which grant, ACL or Azure role scope"
        path="/api/ai-governance/access"
        emptyMessage="No access rows yet — the ai-catalog sync job records grants, ACLs and Azure role assignments here."
      />
    </div>
  );
}

function UsageMonitor() {
  const [days, setDays] = useState(30);
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-1 text-xs">
        <span className="mr-1 text-muted">Usage window:</span>
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
        title="AI app usage"
        subtitle={`Requests, errors, tokens, peak requesters and worst p95 latency per app and endpoint, last ${days} days`}
        path="/api/ai-governance/monitor"
        params={{ days }}
        emptyMessage="No AI usage rows yet — the scheduled ai-monitor rollup populates this view."
      />
    </div>
  );
}

/** AI governance gets a top-level home: the cross-source inventory and access
 * graph the ai-catalog job maintains, the per-app usage rollup, and the live
 * serving/model/GPU hygiene audits. */
export function AiGovernance() {
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab") ?? "inventory";
  const active = AI_TABS.some((tab) => tab.id === requested) ? requested : "inventory";
  const setActive = (tab: string) => {
    const next = new URLSearchParams(params);
    if (tab === "inventory") next.delete("tab");
    else next.set("tab", tab);
    setParams(next, { replace: true });
  };

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Accountable AI"
        title="AI Governance"
        description="Know every model that exists, who can reach it, how it is used, and whether serving stays healthy — across Databricks and Azure AI."
      />
      <Tabs tabs={AI_TABS} active={active} onChange={setActive} label="AI governance views" />
      <div role="tabpanel">
        {active === "inventory" && <InventoryAccess />}
        {active === "monitor" && <UsageMonitor />}
        {active === "serving" && <AiMl />}
      </div>
    </div>
  );
}

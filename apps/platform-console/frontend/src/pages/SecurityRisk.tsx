import { KeyRound, Network, ScanSearch, ShieldCheck, UserRoundCog } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { FindingsSection } from "../components/FindingsSection";
import { Card, PageHeader, Tabs } from "../components/ui";
import { Governance } from "./Governance";
import { Security } from "./Security";

const SECURITY_TABS = [
  { id: "overview", label: "Overview" },
  { id: "identity", label: "Identity & credentials" },
  { id: "governance", label: "Governance" },
  { id: "risk", label: "Risk signals" },
];

const DOMAINS = [
  {
    icon: KeyRound,
    title: "Identity & credentials",
    description: "PAT age, inactive users and over-scoped service principals",
    tab: "identity",
  },
  {
    icon: UserRoundCog,
    title: "Access governance",
    description: "Privileged grants, ownership gaps and policy drift",
    tab: "governance",
  },
  {
    icon: Network,
    title: "Network & egress",
    description: "Public exposure, unrestricted egress and destination drift",
    tab: "risk",
  },
  {
    icon: ScanSearch,
    title: "Audit intelligence",
    description: "Unusual administrative and data-access activity",
    tab: "risk",
  },
];

function RiskSignals() {
  return (
    <div className="space-y-4">
      <FindingsSection
        title="Privileged access drift"
        subtitle="Unexpected account, workspace and Unity Catalog privileges"
        path="/api/security/privilege-drift"
        emptyMessage="No privileged-access drift."
      />
      <FindingsSection
        title="Service principal scope"
        subtitle="Unused credentials, excess grants and missing owners"
        path="/api/security/service-principals"
        emptyMessage="No service-principal scope finding."
      />
      <FindingsSection
        title="Network and egress"
        subtitle="Public access and serverless egress policy coverage"
        path="/api/security/network-egress"
        emptyMessage="No network or egress finding."
      />
      <FindingsSection
        title="Audit anomalies"
        subtitle="Unusual privileged, credential and data-access events"
        path="/api/security/audit-anomalies"
        emptyMessage="No audit anomaly needs attention."
      />
    </div>
  );
}

export function SecurityRisk() {
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab") ?? "overview";
  const active = SECURITY_TABS.some((tab) => tab.id === requested) ? requested : "overview";
  const setActive = (tab: string) => {
    const next = new URLSearchParams(params);
    if (tab === "overview") next.delete("tab");
    else next.set("tab", tab);
    setParams(next, { replace: true });
  };

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Trust"
        title="Security & Risk"
        description="Connect identity, access, policy, network and audit evidence without allowing AI to mutate controls."
      />
      <Tabs tabs={SECURITY_TABS} active={active} onChange={setActive} label="Security and risk views" />
      <div role="tabpanel">
        {active === "overview" && (
          <div className="grid gap-3 md:grid-cols-2">
            {DOMAINS.map(({ icon: Icon, title, description, tab }) => (
              <Link
                key={title}
                to={`/security?tab=${tab}`}
                className="group rounded-2xl focus:outline-none"
              >
                <Card className="h-full transition-transform group-hover:-translate-y-0.5 group-focus-visible:ring-2 group-focus-visible:ring-accent">
                  <div className="flex items-start gap-3">
                    <span className="rounded-xl bg-accent/10 p-2 text-accent">
                      <Icon className="h-4 w-4" />
                    </span>
                    <div>
                      <h2 className="text-sm font-semibold text-ink">{title}</h2>
                      <p className="mt-1 text-xs leading-5 text-muted">{description}</p>
                    </div>
                  </div>
                </Card>
              </Link>
            ))}
            <Card className="md:col-span-2">
              <div className="flex items-start gap-3">
                <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-status-good" />
                <div>
                  <h2 className="text-sm font-semibold text-ink">Control principle</h2>
                  <p className="mt-1 text-xs leading-5 text-ink-2">
                    Detection runs autonomously. Credential, grant, policy and network changes
                    always pass through an immutable, expiring human-approved plan.
                  </p>
                </div>
              </div>
            </Card>
          </div>
        )}
        {active === "identity" && <Security />}
        {active === "governance" && <Governance />}
        {active === "risk" && <RiskSignals />}
      </div>
    </div>
  );
}

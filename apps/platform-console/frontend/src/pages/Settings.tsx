import { useQuery } from "@tanstack/react-query";
import { Eye, KeyRound, LockKeyhole, Settings2, UserCheck } from "lucide-react";
import { BudgetPlanButton } from "../components/BudgetPlanButton";
import { Badge, Card, ErrorState, PageHeader, SectionTitle, Skeleton } from "../components/ui";
import { apiGet } from "../lib/api";
import type { HealthResponse } from "../lib/types";

const ROLES = [
  {
    icon: Eye,
    name: "Viewer",
    description: "Read masked findings, cost, health and verified outcomes.",
  },
  {
    icon: Settings2,
    name: "Operator / proposer",
    description: "Investigate evidence and create deterministic plans without executing.",
  },
  {
    icon: UserCheck,
    name: "Approver",
    description: "Approve one immutable, current plan; self-approval is allowed in v1.",
  },
  {
    icon: LockKeyhole,
    name: "Executor identity",
    description: "Machine-only least privilege; cannot propose or approve its own work.",
  },
];

export function Settings() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => apiGet<HealthResponse>("/api/health"),
    staleTime: 300_000,
    retry: false,
  });
  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Configuration"
        title="Settings"
        description="Review governance boundaries and deployment posture. Configuration changes use the same approval service as resource changes."
      />

      <Card>
        <SectionTitle title="Deployment posture" />
        {health.isPending ? (
          <Skeleton rows={3} />
        ) : health.isError ? (
          <ErrorState error={health.error} />
        ) : (
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="rounded-xl border border-grid bg-page/30 p-3">
              <p className="text-[11px] text-muted">Service</p>
              <p className="mt-1 text-sm font-semibold text-ink">v{health.data.version}</p>
            </div>
            <div className="rounded-xl border border-grid bg-page/30 p-3">
              <p className="text-[11px] text-muted">API health</p>
              <div className="mt-1">
                <Badge tone={health.data.status === "ok" ? "good" : "warning"}>
                  {health.data.status}
                </Badge>
              </div>
            </div>
            <div className="rounded-xl border border-grid bg-page/30 p-3">
              <p className="text-[11px] text-muted">Action mode</p>
              <div className="mt-1">
                <Badge tone={health.data.actions_enabled ? "warning" : "info"}>
                  {health.data.actions_enabled ? "executor enabled" : "proposal only"}
                </Badge>
              </div>
            </div>
          </div>
        )}
      </Card>

      <section aria-labelledby="roles-title">
        <div className="mb-3">
          <h2 id="roles-title" className="text-sm font-semibold text-ink">
            Separation of duties
          </h2>
          <p className="mt-0.5 text-xs text-muted">
            Authorization is verified server-side; forwarded email alone is never sufficient.
          </p>
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          {ROLES.map(({ icon: Icon, name, description }) => (
            <Card key={name}>
              <div className="flex items-start gap-3">
                <span className="rounded-xl bg-accent/10 p-2 text-accent">
                  <Icon className="h-4 w-4" />
                </span>
                <div>
                  <h3 className="text-sm font-semibold text-ink">{name}</h3>
                  <p className="mt-1 text-xs leading-5 text-muted">{description}</p>
                </div>
              </div>
            </Card>
          ))}
        </div>
      </section>

      <Card>
        <SectionTitle
          title="Cost guardrails"
          subtitle="Default alerts at 80% and 100%; alerts never mutate a resource"
          right={<BudgetPlanButton label="Plan guardrail change" />}
        />
        <div className="flex items-start gap-2 text-xs leading-5 text-ink-2">
          <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
          Approver membership, executor credentials and data-source grants are administered in
          Databricks rather than stored in the browser.
        </div>
      </Card>
    </div>
  );
}

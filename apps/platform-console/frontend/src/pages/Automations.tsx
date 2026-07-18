import { BookOpenCheck, CalendarClock, FileText, Workflow } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { PlanActionButton } from "../components/ActionPlanDialog";
import { NamedJobPlanButton } from "../components/NamedJobPlanButton";
import { Card, PageHeader, Tabs } from "../components/ui";
import { Digest } from "./Digest";
import { Jobs } from "./Jobs";

const TABS = [
  { id: "schedules", label: "Jobs & schedules" },
  { id: "briefings", label: "AI briefings" },
  { id: "playbooks", label: "Playbooks" },
];

const PLAYBOOKS = [
  {
    icon: CalendarClock,
    title: "Hibernate toolkit",
    description: "Pause owned schedules, drain runs, stop the dedicated warehouse, then stop the app.",
    action: "hibernate",
    tone: "danger" as const,
  },
  {
    icon: BookOpenCheck,
    title: "Synchronize policies",
    description: "Compare Git-managed policy intent with workspace state and plan exact updates.",
    action: "policy-sync",
    tone: "default" as const,
  },
  {
    icon: Workflow,
    title: "Refresh operational evidence",
    description: "Run the reporting cycle and normalize its observations into Mission Control findings.",
    action: "run-platform-digest",
    tone: "default" as const,
  },
];

export function Automations() {
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab") ?? "schedules";
  const active = TABS.some((tab) => tab.id === requested) ? requested : "schedules";
  const setActive = (tab: string) => {
    const next = new URLSearchParams(params);
    if (tab === "schedules") next.delete("tab");
    else next.set("tab", tab);
    setParams(next, { replace: true });
  };

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Automation"
        title="Automations"
        description="Let evidence collection run continuously while keeping every state-changing run behind human approval."
      />
      <Tabs tabs={TABS} active={active} onChange={setActive} label="Automation views" />
      <div role="tabpanel">
        {active === "schedules" && <Jobs />}
        {active === "briefings" && <Digest />}
        {active === "playbooks" && (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {PLAYBOOKS.map(({ icon: Icon, title, description, action, tone }) => (
              <Card key={action}>
                <span className="inline-flex rounded-xl bg-accent/10 p-2 text-accent">
                  <Icon className="h-4 w-4" />
                </span>
                <h2 className="mt-3 text-sm font-semibold text-ink">{title}</h2>
                <p className="mt-1 min-h-15 text-xs leading-5 text-muted">{description}</p>
                <div className="mt-3">
                  {action === "run-platform-digest" ? (
                    <NamedJobPlanButton
                      expectedName="[dbx-platform] platform-digest"
                      label={`Plan ${title.toLowerCase()}`}
                      tone={tone}
                    />
                  ) : (
                    <PlanActionButton
                      action={action}
                      label={`Plan ${title.toLowerCase()}`}
                      allowLegacy={action === "policy-sync"}
                      tone={tone}
                    />
                  )}
                </div>
              </Card>
            ))}
            <Card className="md:col-span-2 xl:col-span-3">
              <div className="flex items-start gap-3">
                <FileText className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
                <div>
                  <h2 className="text-sm font-semibold text-ink">Autonomy boundary</h2>
                  <p className="mt-1 text-xs leading-5 text-ink-2">
                    Scheduled jobs may read platform state and append findings, telemetry and audit
                    records. Training, promotion, configuration, remediation and runtime control
                    always produce an expiring plan for an authorized human.
                  </p>
                </div>
              </div>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}

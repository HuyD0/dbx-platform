import { BookOpenText, CircleDollarSign, ShieldCheck, Sparkles, Tags } from "lucide-react";
import { Link } from "react-router-dom";
import { Card, PageHeader, SectionTitle } from "../components/ui";
import { Dashboards } from "./Dashboards";

const MODULES = [
  {
    icon: CircleDollarSign,
    title: "Read your first bill",
    description:
      "Databricks list cost and Azure billed actuals are different money with different bases — start with the Cost overview and never sum them.",
    to: "/cost",
    cta: "Open Cost",
  },
  {
    icon: Tags,
    title: "Tag your workloads",
    description:
      "Cluster policies enforce team and project tags so spend can be attributed. Check compliance and fix the gaps recommendations find.",
    to: "/data-governance",
    cta: "Open Data Governance",
  },
  {
    icon: Sparkles,
    title: "Govern AI access",
    description:
      "Every model, endpoint and Azure OpenAI deployment is inventoried with who can invoke it. Review key-auth exposure first.",
    to: "/ai-governance",
    cta: "Open AI Governance",
  },
  {
    icon: ShieldCheck,
    title: "Approve an action safely",
    description:
      "Nothing changes without an immutable, expiring plan and a human approval. Learn the lifecycle in the Action Center blueprint.",
    to: "/actions",
    cta: "Open Action Center",
  },
];

/** Enablement home for a team new to Databricks and Azure AI: task-oriented
 * starting points plus the bundle's exploratory AI/BI dashboards. */
export function Learn() {
  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Enablement"
        title="Learn"
        description="Task-oriented starting points for a team new to Databricks and Azure AI, plus the deep-dive dashboards for open-ended exploration."
      />
      <div className="grid gap-3 md:grid-cols-2">
        {MODULES.map(({ icon: Icon, title, description, to, cta }) => (
          <Card key={title} className="flex h-full flex-col">
            <div className="flex items-start gap-3">
              <span className="rounded-xl bg-accent/10 p-2 text-accent">
                <Icon className="h-4 w-4" />
              </span>
              <div>
                <h2 className="text-sm font-semibold text-ink">{title}</h2>
                <p className="mt-1 text-xs leading-5 text-muted">{description}</p>
              </div>
            </div>
            <div className="mt-3 pt-1">
              <Link
                to={to}
                className="inline-flex items-center gap-1 rounded-lg border border-grid px-3 py-1.5 text-xs font-medium text-ink hover:bg-hairline"
              >
                {cta}
              </Link>
            </div>
          </Card>
        ))}
      </div>
      <Card>
        <SectionTitle
          title="Go deeper"
          subtitle="These app screens are for decisions; the dashboards below are for exploration — slice by team, resource group, principal or app"
        />
        <p className="flex items-start gap-2 text-xs leading-5 text-ink-2">
          <BookOpenText className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
          <span>
            The FinOps Foundation&apos;s free training (including Introduction to FOCUS) pairs
            well with these views — the cost screens use the same vocabulary: list cost vs
            billed actuals, allocation, unit economics.
          </span>
        </p>
      </Card>
      <Dashboards />
    </div>
  );
}

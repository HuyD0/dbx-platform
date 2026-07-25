import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { Tabs } from "./ui";

export const COST_TABS = [
  { id: "overview", label: "Overview" },
  { id: "databricks", label: "Databricks" },
  { id: "categories", label: "Azure" },
  { id: "llm", label: "AI costs" },
  { id: "forecast", label: "Budgets & forecasts" },
  { id: "ownership", label: "Ownership" },
  { id: "alignment", label: "Billing alignment" },
  { id: "coverage", label: "Data coverage" },
  { id: "estimator", label: "Estimator" },
];

export function CostTabs() {
  const location = useLocation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const requested = params.get("tab") ?? "overview";
  const active = location.pathname.startsWith("/cost/estimator")
    ? "estimator"
    : COST_TABS.some((tab) => tab.id === requested && tab.id !== "estimator")
      ? requested
      : "overview";

  const change = (tab: string) => {
    if (tab === "estimator") {
      navigate("/cost/estimator");
      return;
    }
    navigate(tab === "overview" ? "/cost" : `/cost?tab=${encodeURIComponent(tab)}`);
  };

  return (
    <Tabs
      tabs={COST_TABS}
      active={active}
      onChange={change}
      label="Cost Explorer views"
    />
  );
}

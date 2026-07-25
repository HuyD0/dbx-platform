import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { Tabs } from "./ui";

export const COST_TABS = [
  { id: "categories", label: "Service categories" },
  { id: "databricks", label: "Databricks drivers" },
  { id: "ownership", label: "Ownership" },
  { id: "alignment", label: "Billing alignment" },
  { id: "forecast", label: "Forecast & budgets" },
  { id: "coverage", label: "Coverage" },
  { id: "llm", label: "LLM detail" },
  { id: "estimator", label: "Estimator" },
];

export function CostTabs() {
  const location = useLocation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const requested = params.get("tab") ?? "categories";
  const active = location.pathname.startsWith("/cost/estimator")
    ? "estimator"
    : COST_TABS.some((tab) => tab.id === requested && tab.id !== "estimator")
      ? requested
      : "categories";

  const change = (tab: string) => {
    if (tab === "estimator") {
      navigate("/cost/estimator");
      return;
    }
    navigate(tab === "categories" ? "/cost" : `/cost?tab=${encodeURIComponent(tab)}`);
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

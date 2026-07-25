import { useSearchParams } from "react-router-dom";
import { PageHeader, Tabs } from "../components/ui";
import { Housekeeping } from "./Housekeeping";
import { Performance } from "./Performance";

const OPS_TABS = [
  { id: "performance", label: "Performance" },
  { id: "hygiene", label: "Hygiene" },
];

/** Day-2 regression watch and compute hygiene for the workspace. */
export function Operations() {
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab") ?? "performance";
  const active = OPS_TABS.some((tab) => tab.id === requested) ? requested : "performance";
  const setActive = (tab: string) => {
    const next = new URLSearchParams(params);
    if (tab === "performance") next.delete("tab");
    else next.set("tab", tab);
    setParams(next, { replace: true });
  };

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Lifecycle"
        title="Operations"
        description="Performance regressions and compute hygiene — curated evidence collection is autonomous, every change is an approved plan."
      />
      <Tabs tabs={OPS_TABS} active={active} onChange={setActive} label="Operations views" />
      <div role="tabpanel">
        {active === "performance" && <Performance />}
        {active === "hygiene" && <Housekeeping />}
      </div>
    </div>
  );
}

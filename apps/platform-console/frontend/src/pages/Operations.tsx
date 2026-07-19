import { useSearchParams } from "react-router-dom";
import { PageHeader, Tabs } from "../components/ui";
import { Housekeeping } from "./Housekeeping";
import { Performance } from "./Performance";
import { ResourcesRuntime } from "./ResourcesRuntime";

const OPS_TABS = [
  { id: "runtime", label: "Runtime" },
  { id: "performance", label: "Performance" },
  { id: "hygiene", label: "Hygiene" },
];

/** Day-2 operations in one place: runtime lifecycle, regression watch and
 * compute hygiene for everything the toolkit owns. */
export function Operations() {
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab") ?? "runtime";
  const active = OPS_TABS.some((tab) => tab.id === requested) ? requested : "runtime";
  const setActive = (tab: string) => {
    const next = new URLSearchParams(params);
    if (tab === "runtime") next.delete("tab");
    else next.set("tab", tab);
    setParams(next, { replace: true });
  };

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Lifecycle"
        title="Operations"
        description="Runtime state, performance regressions and compute hygiene — detection is autonomous, every change is an approved plan."
      />
      <Tabs tabs={OPS_TABS} active={active} onChange={setActive} label="Operations views" />
      <div role="tabpanel">
        {active === "runtime" && <ResourcesRuntime />}
        {active === "performance" && <Performance />}
        {active === "hygiene" && <Housekeeping />}
      </div>
    </div>
  );
}

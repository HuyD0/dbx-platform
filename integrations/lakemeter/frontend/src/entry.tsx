import { createRoot, type Root } from "react-dom/client";
import {
  BrowserRouter,
  Link,
  Outlet,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { useEffect, useState } from "react";
import toast, { Toaster } from "react-hot-toast";

import Calculator from "../../../../vendor/lakemeter/frontend/src/pages/Calculator";
import EstimateDetail from "../../../../vendor/lakemeter/frontend/src/pages/EstimateDetail";
import Estimates from "../../../../vendor/lakemeter/frontend/src/pages/Estimates";
import Pricing from "../../../../vendor/lakemeter/frontend/src/pages/Pricing";
import { ChatPanel } from "../../../../vendor/lakemeter/frontend/src/components/ChatPanel";
import {
  SESSION_EXPIRED_EVENT,
  resetSessionExpiredFlag,
} from "../../../../vendor/lakemeter/frontend/src/api/client";
import { useStore } from "../../../../vendor/lakemeter/frontend/src/store/useStore";
import "./embedded.css";

export interface LakeMeterOptions {
  dark: boolean;
  assistantOpen: boolean;
  onAssistantClose: () => void;
}

export interface LakeMeterController {
  update: (options: LakeMeterOptions) => void;
  unmount: () => void;
}

const navigation = [
  { label: "Estimates", to: "/" },
  { label: "New estimate", to: "/calculator" },
  { label: "Pricing", to: "/pricing" },
];

function EmbeddedLayout({
  assistantOpen,
  onAssistantClose,
}: Omit<LakeMeterOptions, "dark">) {
  const location = useLocation();
  const [sessionExpired, setSessionExpired] = useState<string | null>(null);
  const {
    authError,
    calculateAllWorkloadCosts,
    createLineItem,
    currentEstimate,
    fetchCurrentUser,
    fetchReferenceData,
    isPricingBundleLoaded,
    isReferenceDataLoaded,
    lineItems,
    loadPricingBundle,
    localCalculatedCosts,
    setSessionExpired: setStoreSessionExpired,
  } = useStore();

  useEffect(() => {
    fetchCurrentUser();
  }, [fetchCurrentUser]);

  useEffect(() => {
    if (!isReferenceDataLoaded) fetchReferenceData();
    if (!isPricingBundleLoaded) loadPricingBundle();
  }, [
    fetchReferenceData,
    isPricingBundleLoaded,
    isReferenceDataLoaded,
    loadPricingBundle,
  ]);

  useEffect(() => {
    const onExpired = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      setSessionExpired(detail?.message || "Your Databricks session has expired.");
      setStoreSessionExpired(true);
      onAssistantClose();
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, [onAssistantClose, setStoreSessionExpired]);

  const editingEstimate =
    location.pathname.startsWith("/calculator/") &&
    location.pathname !== "/calculator";

  if (authError) {
    return (
      <div className="card p-6 text-sm text-[var(--text-secondary)]" role="alert">
        <h2 className="text-base font-semibold text-[var(--text-primary)]">
          Estimator authentication unavailable
        </h2>
        <p className="mt-2">{authError}</p>
      </div>
    );
  }

  return (
    <>
      {sessionExpired && (
        <div
          className="mb-4 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm"
          role="alert"
        >
          {sessionExpired}{" "}
          <button
            type="button"
            className="font-semibold text-lava-600 underline"
            onClick={() => {
              resetSessionExpiredFlag();
              window.location.reload();
            }}
          >
            Refresh
          </button>
        </div>
      )}
      <nav className="lakemeter-subnav" aria-label="Estimator">
        {navigation.map((item) => {
          const active =
            item.to === "/"
              ? location.pathname === "/"
              : location.pathname.startsWith(item.to);
          return (
            <Link key={item.to} to={item.to} data-active={active}>
              {item.label}
            </Link>
          );
        })}
      </nav>
      <Outlet />
      <p className="lakemeter-attribution">Powered by LakeMeter OSS</p>
      <ChatPanel
        isOpen={assistantOpen}
        onClose={onAssistantClose}
        currentEstimate={editingEstimate ? currentEstimate : undefined}
        currentWorkloads={editingEstimate ? lineItems : undefined}
        itemCosts={editingEstimate ? localCalculatedCosts : undefined}
        mode={editingEstimate ? "estimate" : "home"}
        onWorkloadConfirmed={
          editingEstimate
            ? async (workloadConfig) => {
                if (!currentEstimate?.estimate_id) return;
                try {
                  await createLineItem({
                    estimate_id: currentEstimate.estimate_id,
                    ...workloadConfig,
                  });
                  calculateAllWorkloadCosts(currentEstimate.estimate_id);
                  toast.success(`Workload "${workloadConfig.workload_name}" added.`);
                } catch (error) {
                  toast.error(
                    error instanceof Error ? error.message : "Failed to add workload.",
                  );
                  throw error;
                }
              }
            : undefined
        }
      />
      <Toaster position="bottom-right" />
    </>
  );
}

function EmbeddedApp({ dark, assistantOpen, onAssistantClose }: LakeMeterOptions) {
  return (
    <div className={`lakemeter-root${dark ? " dark" : ""}`}>
      <BrowserRouter basename="/cost/estimator">
        <Routes>
          <Route
            path="/"
            element={
              <EmbeddedLayout
                assistantOpen={assistantOpen}
                onAssistantClose={onAssistantClose}
              />
            }
          >
            <Route index element={<Estimates />} />
            <Route path="calculator" element={<Calculator />} />
            <Route path="calculator/:id" element={<Calculator />} />
            <Route path="estimate/:id" element={<EstimateDetail />} />
            <Route path="pricing" element={<Pricing />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </div>
  );
}

export function mountLakeMeter(
  container: HTMLElement,
  initialOptions: LakeMeterOptions,
): LakeMeterController {
  const root: Root = createRoot(container);
  let options = initialOptions;
  const render = () => root.render(<EmbeddedApp {...options} />);
  render();
  return {
    update(next) {
      options = next;
      render();
    },
    unmount() {
      root.unmount();
    },
  };
}

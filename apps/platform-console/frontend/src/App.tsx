import { useQuery } from "@tanstack/react-query";
import {
  Bot,
  CircleDollarSign,
  Gauge,
  LayoutDashboard,
  ListChecks,
  Menu,
  Moon,
  Power,
  ScrollText,
  Settings,
  ShieldCheck,
  Sparkles,
  Sun,
  Workflow,
  X,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AssistantLauncher, AssistantPanel } from "./components/AssistantPanel";
import { Badge } from "./components/ui";
import { apiGet } from "./lib/api";
import { AssistantPanelProvider } from "./lib/assistant-panel";
import { ChatProvider } from "./lib/chat";
import type { HealthResponse } from "./lib/types";
import { ActionCenter } from "./pages/ActionCenter";
import { Audit } from "./pages/Audit";
import { Automations } from "./pages/Automations";
import { Chat } from "./pages/Chat";
import { CostValue } from "./pages/CostValue";
import { MissionControl } from "./pages/MissionControl";
import { Performance } from "./pages/Performance";
import { ResourcesRuntime } from "./pages/ResourcesRuntime";
import { SecurityRisk } from "./pages/SecurityRisk";
import { Settings as SettingsPage } from "./pages/Settings";

interface NavItem {
  to: string;
  label: string;
  icon: typeof LayoutDashboard;
  page: ReactNode;
}

const NAV: NavItem[] = [
  { to: "/", label: "Mission Control", icon: LayoutDashboard, page: <MissionControl /> },
  { to: "/actions", label: "Action Center", icon: ListChecks, page: <ActionCenter /> },
  { to: "/cost", label: "Cost & Value", icon: CircleDollarSign, page: <CostValue /> },
  { to: "/security", label: "Security & Risk", icon: ShieldCheck, page: <SecurityRisk /> },
  { to: "/performance", label: "Performance", icon: Gauge, page: <Performance /> },
  { to: "/runtime", label: "Resources & Runtime", icon: Power, page: <ResourcesRuntime /> },
  { to: "/automations", label: "Automations", icon: Workflow, page: <Automations /> },
  { to: "/assistant", label: "Assistant", icon: Bot, page: <Chat /> },
];

const UTILITY_NAV: NavItem[] = [
  { to: "/settings", label: "Settings", icon: Settings, page: <SettingsPage /> },
  { to: "/audit", label: "Audit", icon: ScrollText, page: <Audit /> },
];

function useTheme() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try {
      localStorage.setItem("theme", dark ? "dark" : "light");
    } catch {
      // Private browsing may prevent persistence; the active theme still works.
    }
  }, [dark]);
  return { dark, toggle: () => setDark((value) => !value) };
}

function Brand() {
  return (
    <div className="flex items-center gap-2.5">
      <span className="brand-diamond relative grid h-8 w-8 place-items-center">
        <Sparkles className="h-4 w-4" />
      </span>
      <div>
        <div className="text-sm font-semibold tracking-tight text-ink">Mission Control</div>
        <div className="text-[10px] uppercase tracking-[0.12em] text-muted">dbx-platform</div>
      </div>
    </div>
  );
}

function Navigation({
  health,
  dark,
  toggleTheme,
  onNavigate,
}: {
  health?: HealthResponse;
  dark: boolean;
  toggleTheme: () => void;
  onNavigate?: () => void;
}) {
  const links = (items: NavItem[]) =>
    items.map(({ to, label, icon: Icon }) => (
      <NavLink
        key={to}
        to={to}
        end={to === "/"}
        onClick={onNavigate}
        className={({ isActive }) =>
          `group flex items-center gap-2.5 rounded-xl px-2.5 py-2 text-[13px] font-medium transition-colors ${
            isActive
              ? "bg-accent/12 text-accent"
              : "text-ink-2 hover:bg-hairline hover:text-ink"
          }`
        }
      >
        {({ isActive }) => (
          <>
            <Icon className={`h-4 w-4 shrink-0 ${isActive ? "text-accent" : "text-muted group-hover:text-ink-2"}`} />
            <span className="truncate">{label}</span>
          </>
        )}
      </NavLink>
    ));

  return (
    <>
      <div className="px-2">
        <Brand />
      </div>
      <div className="mt-5 rounded-xl border border-grid bg-page/35 px-2.5 py-2">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-[11px] font-medium text-ink">Current workspace</span>
          <Badge tone="info">{health?.environment ?? "unknown"}</Badge>
        </div>
        <p className="mt-1 truncate text-[10px] text-muted">Single-workspace control plane</p>
      </div>
      <nav className="mt-4 flex-1 space-y-0.5 overflow-y-auto" aria-label="Primary">
        {links(NAV)}
      </nav>
      <nav className="mt-3 space-y-0.5 border-t border-grid pt-3" aria-label="Utility">
        {links(UTILITY_NAV)}
      </nav>
      <div className="mt-3 space-y-2 border-t border-grid px-2 pt-3">
        {health && (
          <Badge tone={health.actions_enabled ? "warning" : "info"}>
            {health.actions_enabled ? "executor enabled" : "proposal only"}
          </Badge>
        )}
        <div className="flex items-center justify-between text-[11px] text-muted">
          <span>{health ? `v${health.version}` : ""}</span>
          <button
            type="button"
            onClick={toggleTheme}
            aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
            className="rounded-lg p-1.5 hover:bg-hairline"
          >
            {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
        </div>
      </div>
    </>
  );
}

export default function App() {
  const { dark, toggle } = useTheme();
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const mobileTriggerRef = useRef<HTMLButtonElement>(null);
  const mobileCloseRef = useRef<HTMLButtonElement>(null);
  const mobileDrawerRef = useRef<HTMLElement>(null);
  const location = useLocation();
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => apiGet<HealthResponse>("/api/health"),
    staleTime: 300_000,
    retry: false,
  });

  useEffect(() => {
    if (["/chat", "/assistant"].includes(location.pathname)) setAssistantOpen(false);
    setMobileNavOpen(false);
    const item = [...NAV, ...UTILITY_NAV].find(
      ({ to }) => location.pathname === to || (to !== "/" && location.pathname.startsWith(`${to}/`)),
    );
    document.title = `${item?.label ?? "Mission Control"} · dbx-platform`;
  }, [location.pathname]);

  useEffect(() => {
    if (!mobileNavOpen) return;
    const original = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.requestAnimationFrame(() => mobileCloseRef.current?.focus());
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setMobileNavOpen(false);
        return;
      }
      if (event.key !== "Tab" || !mobileDrawerRef.current) return;
      const focusable = Array.from(
        mobileDrawerRef.current.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", close);
    return () => {
      document.body.style.overflow = original;
      document.removeEventListener("keydown", close);
      mobileTriggerRef.current?.focus();
    };
  }, [mobileNavOpen]);

  return (
    <ChatProvider>
      <AssistantPanelProvider onOpen={() => setAssistantOpen(true)}>
        <a
          href="#main-content"
          onClick={(event) => {
            event.preventDefault();
            window.requestAnimationFrame(() => {
              document.getElementById("main-content")?.focus();
            });
          }}
          className="fixed left-3 top-3 z-[100] -translate-y-20 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-white transition-transform focus:translate-y-0"
        >
          Skip to main content
        </a>

        <div className="min-h-screen">
          <aside
            aria-hidden={assistantOpen || undefined}
            className="glass glass-edge-r fixed inset-y-0 z-30 hidden w-64 flex-col px-3 py-4 lg:flex"
          >
            <Navigation health={health.data} dark={dark} toggleTheme={toggle} />
          </aside>

          <header
            aria-hidden={mobileNavOpen || assistantOpen || undefined}
            className="glass glass-edge-b fixed inset-x-0 top-0 z-30 flex h-15 items-center gap-1 px-2 lg:hidden"
          >
            <button
              ref={mobileTriggerRef}
              type="button"
              onClick={() => setMobileNavOpen(true)}
              aria-label="Open navigation"
              aria-expanded={mobileNavOpen}
              className="grid h-11 w-11 place-items-center rounded-lg text-ink hover:bg-hairline"
            >
              <Menu className="h-5 w-5" />
            </button>
            <Brand />
          </header>

          {mobileNavOpen && (
            <div className="fixed inset-0 z-50 lg:hidden">
              <div
                aria-hidden="true"
                onClick={() => setMobileNavOpen(false)}
                className="absolute inset-0 bg-black/50 backdrop-blur-sm"
              />
              <aside
                ref={mobileDrawerRef}
                role="dialog"
                aria-modal="true"
                className="glass-strong absolute inset-y-0 left-0 flex w-[min(20rem,88vw)] flex-col p-4 shadow-2xl"
                aria-label="Mobile navigation"
              >
                <button
                  ref={mobileCloseRef}
                  type="button"
                  onClick={() => setMobileNavOpen(false)}
                  aria-label="Close navigation"
                  className="absolute right-3 top-3 rounded-lg p-1.5 text-muted hover:bg-hairline"
                >
                  <X className="h-4 w-4" />
                </button>
                <Navigation
                  health={health.data}
                  dark={dark}
                  toggleTheme={toggle}
                  onNavigate={() => setMobileNavOpen(false)}
                />
              </aside>
            </div>
          )}

          <main
            id="main-content"
            tabIndex={-1}
            aria-hidden={mobileNavOpen || assistantOpen || undefined}
            className="px-4 pb-24 pt-20 focus:outline-none sm:px-6 lg:ml-64 lg:px-8 lg:pb-8 lg:pt-6"
          >
            <div className="mx-auto max-w-7xl">
              <Routes>
                {[...NAV, ...UTILITY_NAV].map(({ to, page }) => (
                  <Route key={to} path={to} element={page} />
                ))}
                <Route path="/overview" element={<Navigate to="/" replace />} />
                <Route path="/chat" element={<Navigate to="/assistant" replace />} />
                <Route path="/housekeeping" element={<Navigate to="/actions" replace />} />
                <Route path="/governance" element={<Navigate to="/security?tab=governance" replace />} />
                <Route path="/ai-ml" element={<Navigate to="/cost?tab=llm" replace />} />
                <Route path="/digest" element={<Navigate to="/automations?tab=briefings" replace />} />
                <Route path="/jobs" element={<Navigate to="/automations" replace />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </div>
          </main>

          {!mobileNavOpen && <AssistantLauncher onOpen={() => setAssistantOpen(true)} />}
          <AssistantPanel open={assistantOpen} onClose={() => setAssistantOpen(false)} />
        </div>
      </AssistantPanelProvider>
    </ChatProvider>
  );
}

import { useQuery } from "@tanstack/react-query";
import {
  Bot,
  DollarSign,
  Gauge,
  LayoutDashboard,
  ListChecks,
  Moon,
  Newspaper,
  Scale,
  Shield,
  Sparkles,
  Sun,
} from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { AssistantLauncher, AssistantPanel } from "./components/AssistantPanel";
import { Badge } from "./components/ui";
import { apiGet } from "./lib/api";
import { ChatProvider } from "./lib/chat";
import type { HealthResponse } from "./lib/types";
import { AiMl } from "./pages/AiMl";
import { Chat } from "./pages/Chat";
import { Cost } from "./pages/Cost";
import { Digest } from "./pages/Digest";
import { Governance } from "./pages/Governance";
import { Housekeeping } from "./pages/Housekeeping";
import { Jobs } from "./pages/Jobs";
import { Overview } from "./pages/Overview";
import { Security } from "./pages/Security";

const NAV = [
  { to: "/", label: "Overview", icon: LayoutDashboard, page: <Overview /> },
  { to: "/chat", label: "Assistant", icon: Bot, page: <Chat /> },
  { to: "/cost", label: "Cost", icon: DollarSign, page: <Cost /> },
  { to: "/housekeeping", label: "Housekeeping", icon: Gauge, page: <Housekeeping /> },
  { to: "/security", label: "Security", icon: Shield, page: <Security /> },
  { to: "/governance", label: "Governance", icon: Scale, page: <Governance /> },
  { to: "/ai-ml", label: "AI / ML", icon: Sparkles, page: <AiMl /> },
  { to: "/digest", label: "Digest", icon: Newspaper, page: <Digest /> },
  { to: "/jobs", label: "Jobs", icon: ListChecks, page: <Jobs /> },
];

function useTheme() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try {
      localStorage.setItem("theme", dark ? "dark" : "light");
    } catch {
      // private mode — theme just won't persist
    }
  }, [dark]);
  return { dark, toggle: () => setDark((d) => !d) };
}

export default function App() {
  const { dark, toggle } = useTheme();
  const [assistantOpen, setAssistantOpen] = useState(false);
  const location = useLocation();

  // The full chat page and the slide-over are the same conversation — never
  // show both. Navigating to /chat (e.g. via the sidebar) closes the panel.
  useEffect(() => {
    if (location.pathname === "/chat") setAssistantOpen(false);
  }, [location.pathname]);
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => apiGet<HealthResponse>("/api/health"),
    staleTime: 300_000,
    retry: false,
  });

  return (
    <ChatProvider>
      <div className="flex min-h-screen">
        <aside className="fixed inset-y-0 flex w-52 flex-col border-r border-hairline bg-surface px-3 py-4">
          <div className="mb-6 px-2">
            <div className="text-sm font-semibold text-ink">Platform Console</div>
            <div className="text-[11px] text-muted">dbx-platform</div>
          </div>
          <nav className="flex-1 space-y-0.5">
            {NAV.map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                end={to === "/"}
                className={({ isActive }) =>
                  `flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13px] font-medium ${
                    isActive ? "bg-accent/15 text-accent" : "text-ink-2 hover:bg-hairline"
                  }`
                }
              >
                <Icon className="h-4 w-4" />
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="space-y-2 px-2">
            {health.data && (
              <Badge tone={health.data.actions_enabled ? "warning" : "info"}>
                {health.data.actions_enabled ? "actions enabled" : "report-only"}
              </Badge>
            )}
            <div className="flex items-center justify-between text-[11px] text-muted">
              <span>{health.data ? `v${health.data.version}` : ""}</span>
              <button
                type="button"
                onClick={toggle}
                aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
                className="rounded-lg p-1.5 hover:bg-hairline"
              >
                {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              </button>
            </div>
          </div>
        </aside>
        <main className="ml-52 flex-1 px-6 py-5">
          <div className="mx-auto max-w-5xl">
            <Routes>
              {NAV.map(({ to, page }) => (
                <Route key={to} path={to} element={page} />
              ))}
              <Route path="*" element={<Overview />} />
            </Routes>
          </div>
        </main>
        <AssistantLauncher onOpen={() => setAssistantOpen(true)} />
        <AssistantPanel open={assistantOpen} onClose={() => setAssistantOpen(false)} />
      </div>
    </ChatProvider>
  );
}

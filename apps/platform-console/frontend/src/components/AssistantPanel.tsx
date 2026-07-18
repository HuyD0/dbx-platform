import { Bot, Eraser, X } from "lucide-react";
import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useChat } from "../lib/chat";
import { ChatThread } from "./ChatThread";

/** Slide-over assistant available on every page, sharing the Chat page's
 * conversation. Esc closes it; the header link expands to the full page. */
export function AssistantPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { clear, turns } = useChat();
  const navigate = useNavigate();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="glass-strong fixed inset-y-0 right-0 z-40 flex w-full max-w-md flex-col border-y-0 border-r-0 shadow-2xl">
      <div className="flex items-center justify-between border-b border-grid px-3 py-2">
        <button
          type="button"
          onClick={() => {
            onClose();
            navigate("/chat");
          }}
          className="flex items-center gap-2 rounded-lg px-2 py-1 text-sm font-semibold text-ink hover:bg-hairline"
          title="Open full page"
        >
          <Bot className="h-4 w-4 text-accent" />
          Assistant
        </button>
        <div className="flex items-center gap-1">
          {turns.length > 0 && (
            <button
              type="button"
              onClick={clear}
              title="New conversation"
              aria-label="New conversation"
              className="rounded-lg p-1.5 text-muted hover:bg-hairline"
            >
              <Eraser className="h-4 w-4" />
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            aria-label="Close assistant"
            className="rounded-lg p-1.5 text-muted hover:bg-hairline"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>
      <ChatThread compact />
    </div>
  );
}

/** Floating pill that opens the assistant — hidden on the full chat page. */
export function AssistantLauncher({ onOpen }: { onOpen: () => void }) {
  const location = useLocation();
  if (location.pathname === "/chat") return null;
  return (
    <button
      type="button"
      onClick={onOpen}
      className="fixed bottom-5 right-5 z-30 flex items-center gap-2 rounded-full bg-accent px-4 py-2.5 text-sm font-medium text-white shadow-xl transition-transform hover:scale-105"
    >
      <Bot className="h-4 w-4" />
      Ask agent
    </button>
  );
}

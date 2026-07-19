import { Bot, Eraser, X } from "lucide-react";
import { useEffect, useId, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useChat } from "../lib/chat";
import { ChatThread } from "./ChatThread";

/** Slide-over assistant available on every page, sharing the Chat page's
 * conversation. Esc closes it; the header link expands to the full page. */
export function AssistantPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { clear, turns } = useChat();
  const navigate = useNavigate();
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const previousFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    previousFocus.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    window.requestAnimationFrame(() => closeRef.current?.focus());
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !panelRef.current) return;
      const focusable = Array.from(
        panelRef.current.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
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
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = originalOverflow;
      previousFocus.current?.focus();
    };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      ref={panelRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      className="glass-strong glass-edge-l fixed inset-y-0 right-0 z-40 flex w-full max-w-md flex-col shadow-2xl"
    >
      <div className="flex items-center justify-between border-b border-grid px-3 py-2">
        <button
          type="button"
          onClick={() => {
            onClose();
            navigate("/assistant");
          }}
          className="flex items-center gap-2 rounded-lg px-2 py-1 text-ink hover:bg-hairline"
          title="Open full page"
        >
          <Bot className="h-4 w-4 text-accent" />
          <span className="text-left">
            <span id={titleId} className="block text-sm font-semibold">
              Read-only investigator
            </span>
            <span className="block text-[10px] font-normal text-muted">
              Workspace evidence · proposal only
            </span>
          </span>
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
            ref={closeRef}
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
  if (["/chat", "/assistant"].includes(location.pathname)) return null;
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label="Ask agent"
      className="fixed bottom-4 right-4 z-30 flex h-11 w-11 items-center justify-center gap-2 rounded-full bg-accent text-sm font-medium text-white shadow-xl transition-transform hover:scale-105 sm:bottom-5 sm:right-5 sm:h-auto sm:w-auto sm:px-4 sm:py-2.5"
    >
      <Bot className="h-4 w-4" />
      <span className="hidden sm:inline">Ask agent</span>
    </button>
  );
}

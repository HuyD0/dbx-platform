import {
  Bot,
  Brain,
  Database,
  Eraser,
  LockKeyhole,
  MapPin,
  MessageSquare,
  ShieldCheck,
  X,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useChat } from "../lib/chat";
import { ChatThread } from "./ChatThread";

function useDesktopContextLayout(): boolean {
  const [desktop, setDesktop] = useState(
    () =>
      typeof window !== "undefined" && (window.matchMedia?.("(min-width: 640px)").matches ?? false),
  );
  useEffect(() => {
    const media = window.matchMedia?.("(min-width: 640px)");
    if (!media) return;
    const update = () => setDesktop(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return desktop;
}

/** Slide-over assistant available on every page, sharing the Chat page's
 * conversation. Esc closes it; the header link expands to the full page. */
export function AssistantPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { clear, focus, setFocus, turns } = useChat();
  const navigate = useNavigate();
  const location = useLocation();
  const [memoryOpen, setMemoryOpen] = useState(false);
  const desktopContextLayout = useDesktopContextLayout();
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const memoryToggleRef = useRef<HTMLButtonElement>(null);
  const memoryCloseRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const memoryId = useId();
  const previousFocus = useRef<HTMLElement | null>(null);
  const activeFilterCount = Array.from(new URLSearchParams(location.search).entries()).slice(
    0,
    30,
  ).length;
  const mobileMemoryOverlay = memoryOpen && !desktopContextLayout;

  useEffect(() => {
    if (mobileMemoryOverlay) {
      window.requestAnimationFrame(() => memoryCloseRef.current?.focus());
    }
  }, [mobileMemoryOverlay]);

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
      ).filter((element) => !element.closest('[inert], [aria-hidden="true"]'));
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
      className={`glass-strong glass-edge-l fixed inset-y-0 right-0 z-40 flex w-full flex-col shadow-2xl transition-[max-width] duration-200 ${
        memoryOpen ? "max-w-[56rem]" : "max-w-md"
      }`}
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
          <button
            ref={memoryToggleRef}
            type="button"
            onClick={() => setMemoryOpen((value) => !value)}
            aria-controls={memoryId}
            aria-expanded={memoryOpen}
            aria-label={memoryOpen ? "Hide context memory" : "Show context memory"}
            className={`rounded-lg p-1.5 hover:bg-[#F9EAED] ${
              memoryOpen ? "bg-[#F9EAED] text-[#8B001F]" : "text-muted"
            }`}
            title="Context memory"
          >
            <Brain className="h-4 w-4" />
          </button>
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
      <div className="relative flex min-h-0 flex-1">
        <div
          data-testid="assistant-chat-surface"
          className="flex min-w-0 flex-1 flex-col"
          aria-hidden={mobileMemoryOverlay ? true : undefined}
          {...(mobileMemoryOverlay ? { inert: "" } : {})}
        >
          <div className="border-b border-grid px-4 py-2.5">
            <div className="flex items-center justify-between gap-3 text-[11px]">
              <span className="inline-flex items-center gap-1.5 font-medium text-ink-2">
                <LockKeyhole className="h-3.5 w-3.5 text-accent" />
                Read-only — cannot execute changes
              </span>
              {focus && (
                <button
                  type="button"
                  onClick={() => setFocus(null)}
                  className="rounded-md px-1.5 py-1 text-muted hover:bg-hairline hover:text-ink"
                >
                  Clear focus
                </button>
              )}
            </div>
            {focus && (
              <p className="mt-1 truncate text-xs text-ink" title={focus.label}>
                Focused on: {focus.label}
              </p>
            )}
          </div>
          <ChatThread compact />
        </div>

        {memoryOpen && (
          <aside
            id={memoryId}
            aria-labelledby={`${memoryId}-title`}
            className="absolute inset-y-0 right-0 z-10 w-[min(19rem,calc(100%-2rem))] overflow-y-auto border-l border-[#E4D7DB] bg-white shadow-2xl sm:static sm:w-72 sm:shrink-0 sm:shadow-none"
          >
            <div className="flex items-start justify-between gap-3 border-b border-[#E4D7DB] px-4 py-3">
              <div>
                <h3
                  id={`${memoryId}-title`}
                  className="flex items-center gap-2 text-sm font-semibold text-[#240B15]"
                >
                  <Brain className="h-4 w-4 text-[#8B001F]" />
                  Context memory
                </h3>
                <p className="mt-1 text-[10px] text-[#806A72]">Active bounded constraints</p>
              </div>
              <button
                ref={memoryCloseRef}
                type="button"
                onClick={() => {
                  setMemoryOpen(false);
                  window.requestAnimationFrame(() => memoryToggleRef.current?.focus());
                }}
                aria-label="Close context memory"
                className="rounded-lg p-1 text-[#806A72] hover:bg-[#F9EAED] hover:text-[#240B15]"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>

            <div className="space-y-2 p-3">
              <div className="rounded-xl border border-[#E4D7DB] bg-[#FBF7F8] p-3">
                <p className="flex items-center gap-2 text-[11px] font-semibold text-[#240B15]">
                  <MessageSquare className="h-3.5 w-3.5 text-[#00AAAD]" />
                  Session memory
                </p>
                <p className="mt-1 text-xs font-medium text-[#4B3F43]">
                  {turns.length > 50
                    ? `Newest 50 of ${turns.length} messages active`
                    : `${turns.length} message${turns.length === 1 ? "" : "s"} active`}{" "}
                  · 50-message request ceiling
                </p>
                <p className="mt-1 text-[10px] leading-4 text-[#806A72]">
                  Conversation state stays in this browser tab and clears with New conversation.
                </p>
              </div>

              <div className="rounded-xl border border-[#E4D7DB] bg-[#FBF7F8] p-3">
                <p className="flex items-center gap-2 text-[11px] font-semibold text-[#240B15]">
                  <MapPin className="h-3.5 w-3.5 text-[#00AAAD]" />
                  Page scope
                </p>
                <p
                  className="mt-1 truncate text-xs font-medium text-[#4B3F43]"
                  title={location.pathname}
                >
                  {location.pathname}
                </p>
                <p className="mt-1 text-[10px] leading-4 text-[#806A72]">
                  {activeFilterCount} of 30 filter slots · 0 of 20 selected resources
                </p>
              </div>

              <div className="rounded-xl border border-[#E4D7DB] bg-[#FBF7F8] p-3">
                <p className="flex items-center gap-2 text-[11px] font-semibold text-[#240B15]">
                  <Database className="h-3.5 w-3.5 text-[#00AAAD]" />
                  Evidence focus
                </p>
                <p className="mt-1 text-xs font-medium text-[#4B3F43]">
                  {focus?.label ?? "No action selected"}
                </p>
                <p className="mt-1 text-[10px] leading-4 text-[#806A72]">
                  Focus IDs are resolved server-side into read-only evidence, never executor input.
                </p>
              </div>

              <div className="rounded-xl border border-[#E4D7DB] bg-[#F9EAED] p-3">
                <p className="flex items-center gap-2 text-[11px] font-semibold text-[#240B15]">
                  <ShieldCheck className="h-3.5 w-3.5 text-[#72BF44]" />
                  Authority boundary
                </p>
                <p className="mt-1 text-xs font-medium text-[#240B15]">
                  Evidence and proposals only
                </p>
                <p className="mt-1 text-[10px] leading-4 text-[#4B3F43]">
                  No target mutations, approvals, or execution authority are available to this
                  agent.
                </p>
              </div>
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}

/** Floating pill that opens the assistant — hidden on the full chat page. */
export function AssistantLauncher({ onOpen }: { onOpen: () => void }) {
  const location = useLocation();
  const { setFocus } = useChat();
  if (["/", "/chat", "/assistant"].includes(location.pathname)) return null;
  return (
    <button
      type="button"
      onClick={() => {
        setFocus(null);
        onOpen();
      }}
      aria-label="Ask agent"
      className="fixed bottom-4 right-4 z-30 flex h-11 w-11 items-center justify-center gap-2 rounded-full bg-accent text-sm font-medium text-white shadow-xl transition-transform hover:scale-105 sm:bottom-5 sm:right-5 sm:h-auto sm:w-auto sm:px-4 sm:py-2.5"
    >
      <Bot className="h-4 w-4" />
      <span className="hidden sm:inline">Ask agent</span>
    </button>
  );
}

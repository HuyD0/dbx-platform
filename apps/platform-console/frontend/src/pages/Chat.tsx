import { Eraser } from "lucide-react";
import { ChatThread } from "../components/ChatThread";
import { PageHeader } from "../components/ui";
import { useChat } from "../lib/chat";

export function Chat() {
  const { turns, clear } = useChat();
  return (
    <div className="flex min-h-[calc(100vh-2.5rem)] flex-col gap-3">
      <PageHeader
        eyebrow="Read-only investigator"
        title="Assistant"
        description="Ask about the current workspace, compare options, and draft a cited proposal. The assistant cannot execute tools."
        actions={
          turns.length > 0 ? (
            <button
              type="button"
              onClick={clear}
              className="inline-flex items-center gap-1.5 rounded-lg border border-grid px-2.5 py-1 text-xs text-ink-2 hover:bg-hairline"
            >
              <Eraser className="h-3.5 w-3.5" />
              New conversation
            </button>
          ) : undefined
        }
      />
      <div className="min-h-0 flex-1">
        <ChatThread />
      </div>
    </div>
  );
}

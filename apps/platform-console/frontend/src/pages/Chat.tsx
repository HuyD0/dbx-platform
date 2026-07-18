import { Eraser } from "lucide-react";
import { ChatThread } from "../components/ChatThread";
import { useChat } from "../lib/chat";

export function Chat() {
  const { turns, clear } = useChat();
  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col">
      {turns.length > 0 && (
        <div className="flex justify-end">
          <button
            type="button"
            onClick={clear}
            className="inline-flex items-center gap-1.5 rounded-lg border border-grid px-2.5 py-1 text-xs text-ink-2 hover:bg-hairline"
          >
            <Eraser className="h-3.5 w-3.5" />
            New conversation
          </button>
        </div>
      )}
      <ChatThread />
    </div>
  );
}

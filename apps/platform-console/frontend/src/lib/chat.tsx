import { useMutation } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { apiPost } from "./api";
import type { ChatResponse, Proposal } from "./types";

export interface Turn {
  role: "user" | "assistant";
  content: string;
  proposals?: Proposal[];
}

interface ChatState {
  turns: Turn[];
  pending: boolean;
  error: unknown;
  send: (text: string) => void;
  clear: () => void;
}

/** One conversation shared by the Chat page and the slide-over assistant
 * panel, so switching surfaces never loses history. State is client-side
 * only — the backend is stateless. */
const ChatContext = createContext<ChatState | null>(null);

export function ChatProvider({ children }: { children: ReactNode }) {
  const [turns, setTurns] = useState<Turn[]>([]);

  const mutation = useMutation({
    mutationFn: (history: Turn[]) =>
      apiPost<ChatResponse>("/api/chat", {
        messages: history.map(({ role, content }) => ({ role, content })),
      }),
    onSuccess: (resp) =>
      setTurns((t) => [
        ...t,
        { role: "assistant", content: resp.message, proposals: resp.proposals },
      ]),
  });

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || mutation.isPending) return;
      setTurns((prev) => {
        const next: Turn[] = [...prev, { role: "user", content: trimmed }];
        mutation.mutate(next);
        return next;
      });
    },
    [mutation],
  );

  const clear = useCallback(() => {
    setTurns([]);
    mutation.reset();
  }, [mutation]);

  const value = useMemo(
    () => ({ turns, pending: mutation.isPending, error: mutation.error, send, clear }),
    [turns, mutation.isPending, mutation.error, send, clear],
  );
  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

export function useChat(): ChatState {
  const ctx = useContext(ChatContext);
  if (!ctx) throw new Error("useChat outside ChatProvider");
  return ctx;
}

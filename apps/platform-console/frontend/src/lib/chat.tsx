import { useMutation } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { apiPost } from "./api";
import type { AssistantCitation, ChatResponse, Proposal } from "./types";

export interface ChatFocus {
  actionId: string;
  label: string;
}

export interface Turn {
  role: "user" | "assistant";
  content: string;
  proposals?: Proposal[];
  citations?: AssistantCitation[];
  focusActionId?: string;
}

interface ChatState {
  turns: Turn[];
  pending: boolean;
  error: unknown;
  focus: ChatFocus | null;
  send: (text: string, focusOverride?: ChatFocus | null) => void;
  clear: () => void;
  setFocus: (focus: ChatFocus | null) => void;
}

/** One conversation shared by the Chat page and the slide-over assistant
 * panel, so switching surfaces never loses history. State is client-side
 * only — the backend is stateless. */
const ChatContext = createContext<ChatState | null>(null);

export function ChatProvider({ children }: { children: ReactNode }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [focus, setFocus] = useState<ChatFocus | null>(null);
  const location = useLocation();

  const mutation = useMutation({
    mutationFn: ({
      history,
      requestFocus,
    }: {
      history: Turn[];
      requestFocus: ChatFocus | null;
    }) => {
      const search = new URLSearchParams(location.search);
      const filters = Object.fromEntries(
        Array.from(search.entries()).slice(0, 30),
      );
      return apiPost<ChatResponse>("/api/chat", {
        messages: history.map(({ role, content }) => ({ role, content })),
        context: {
          route: location.pathname,
          query: location.search,
          focus_action_id: requestFocus?.actionId,
          filters,
          selected_resources: [],
        },
      });
    },
    onSuccess: (resp, variables) =>
      setTurns((t) => [
        ...t,
        {
          role: "assistant",
          content: resp.message,
          proposals: resp.proposals,
          citations: resp.citations ?? [],
          focusActionId: variables.requestFocus?.actionId,
        },
      ]),
  });

  const send = useCallback(
    (text: string, focusOverride?: ChatFocus | null) => {
      const trimmed = text.trim();
      if (!trimmed || mutation.isPending) return;
      const requestFocus = focusOverride === undefined ? focus : focusOverride;
      setTurns((prev) => {
        const next: Turn[] = [...prev, { role: "user", content: trimmed }];
        mutation.mutate({ history: next, requestFocus });
        return next;
      });
    },
    [focus, mutation],
  );

  const clear = useCallback(() => {
    setTurns([]);
    mutation.reset();
  }, [mutation]);

  const value = useMemo(
    () => ({
      turns,
      pending: mutation.isPending,
      error: mutation.error,
      focus,
      send,
      clear,
      setFocus,
    }),
    [turns, mutation.isPending, mutation.error, focus, send, clear],
  );
  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

export function useChat(): ChatState {
  const ctx = useContext(ChatContext);
  if (!ctx) throw new Error("useChat outside ChatProvider");
  return ctx;
}

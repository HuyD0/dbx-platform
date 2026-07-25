import { createContext, useCallback, useContext, type ReactNode } from "react";
import { useChat, type ChatFocus } from "./chat";

type OpenAssistant = (focus?: ChatFocus) => void;

const AssistantPanelContext = createContext<OpenAssistant | null>(null);

export function AssistantPanelProvider({
  children,
  onOpen,
}: {
  children: ReactNode;
  onOpen: () => void;
}) {
  const { setFocus } = useChat();
  const open = useCallback(
    (focus?: ChatFocus) => {
      setFocus(focus ?? null);
      onOpen();
    },
    [onOpen, setFocus],
  );
  return (
    <AssistantPanelContext.Provider value={open}>
      {children}
    </AssistantPanelContext.Provider>
  );
}

export function useAssistantPanel() {
  const open = useContext(AssistantPanelContext);
  if (!open) throw new Error("useAssistantPanel outside AssistantPanelProvider");
  return open;
}

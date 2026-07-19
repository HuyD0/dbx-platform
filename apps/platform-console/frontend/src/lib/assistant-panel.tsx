import { createContext, useContext, type ReactNode } from "react";

const AssistantPanelContext = createContext<(() => void) | null>(null);

export function AssistantPanelProvider({
  children,
  onOpen,
}: {
  children: ReactNode;
  onOpen: () => void;
}) {
  return (
    <AssistantPanelContext.Provider value={onOpen}>
      {children}
    </AssistantPanelContext.Provider>
  );
}

export function useAssistantPanel() {
  const open = useContext(AssistantPanelContext);
  if (!open) throw new Error("useAssistantPanel outside AssistantPanelProvider");
  return open;
}

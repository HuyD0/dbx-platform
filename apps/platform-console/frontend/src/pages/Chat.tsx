import { useMutation } from "@tanstack/react-query";
import { Bot, Play, Send, User } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { ActionPlanDialog } from "../components/ActionPlanDialog";
import { apiPost } from "../lib/api";
import type { ChatResponse, Proposal } from "../lib/types";
import { Badge, Card, ErrorState } from "../components/ui";

interface Turn {
  role: "user" | "assistant";
  content: string;
  proposals?: Proposal[];
}

const SUGGESTIONS = [
  "Where are we wasting the most money right now?",
  "Audit our serving endpoints and summarize the risks.",
  "Any security findings I should worry about?",
  "Clean up stale clusters.",
];

function JobProposalCard({ proposal }: { proposal: Proposal }) {
  const run = useMutation({
    mutationFn: () => apiPost<{ run_id: number }>(`/api/jobs/${proposal.job_id}/run_now`),
  });
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-grid bg-page px-3 py-2 text-xs">
      <span className="text-ink-2">
        Agent proposes running <span className="font-medium text-ink">{proposal.name}</span>
      </span>
      {run.data ? (
        <Badge tone="good">started run {run.data.run_id}</Badge>
      ) : (
        <button
          type="button"
          onClick={() => run.mutate()}
          disabled={run.isPending}
          className="inline-flex items-center gap-1 rounded-lg border border-grid px-2.5 py-1 font-medium text-ink hover:bg-hairline disabled:opacity-50"
        >
          <Play className="h-3 w-3" />
          Run job
        </button>
      )}
      {run.isError && <ErrorState error={run.error} />}
    </div>
  );
}

function ActionProposalCard({ proposal }: { proposal: Proposal }) {
  const [open, setOpen] = useState(false);
  const action = proposal.action ?? "";
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-grid bg-page px-3 py-2 text-xs">
      <span className="text-ink-2">
        Agent proposes <span className="font-medium text-ink">{action}</span>
        {typeof proposal.count === "number" && ` (${proposal.count} item(s) in its dry-run)`}
      </span>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="rounded-lg border border-grid px-2.5 py-1 font-medium text-ink hover:bg-hairline"
      >
        Review &amp; plan
      </button>
      <span className="text-muted">A fresh dry-run + typed confirmation is always required.</span>
      {open && (
        <ActionPlanDialog action={action} title={`Plan ${action}`} onClose={() => setOpen(false)} />
      )}
    </div>
  );
}

export function Chat() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const bottom = useRef<HTMLDivElement>(null);

  const send = useMutation({
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

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, send.isPending]);

  const submit = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || send.isPending) return;
    const next: Turn[] = [...turns, { role: "user", content: trimmed }];
    setTurns(next);
    setInput("");
    send.mutate(next);
  };

  return (
    <div className="flex h-[calc(100vh-8rem)] flex-col gap-3">
      <Card className="flex-1 overflow-y-auto">
        {turns.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
            <Bot className="h-8 w-8 text-muted" />
            <p className="max-w-md text-sm text-ink-2">
              Ask the platform agent about cost, security, governance or AI/ML health. It
              runs the same read-only checks as the CLI — and when action is needed, it
              proposes it for <span className="font-medium text-ink">you</span> to confirm.
            </p>
            <div className="flex flex-wrap justify-center gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => submit(s)}
                  className="rounded-full border border-grid px-3 py-1.5 text-xs text-ink-2 hover:bg-hairline"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="space-y-4">
          {turns.map((turn, i) => (
            <div key={i} className="flex gap-2">
              <div className="mt-0.5 shrink-0 rounded-full bg-hairline p-1.5">
                {turn.role === "user" ? (
                  <User className="h-3.5 w-3.5 text-ink-2" />
                ) : (
                  <Bot className="h-3.5 w-3.5 text-accent" />
                )}
              </div>
              <div className="min-w-0 flex-1">
                <div className="prose-console text-ink-2">
                  <ReactMarkdown>{turn.content}</ReactMarkdown>
                </div>
                {turn.proposals?.map((p, j) =>
                  p.kind === "job" ? (
                    <JobProposalCard key={j} proposal={p} />
                  ) : (
                    <ActionProposalCard key={j} proposal={p} />
                  ),
                )}
              </div>
            </div>
          ))}
          {send.isPending && (
            <div className="flex items-center gap-2 text-xs text-muted">
              <Bot className="h-3.5 w-3.5 animate-pulse" />
              The agent is running checks — this can take a minute…
            </div>
          )}
          {send.isError && <ErrorState error={send.error} />}
          <div ref={bottom} />
        </div>
      </Card>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit(input);
        }}
        className="flex gap-2"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask the platform agent…"
          aria-label="Message the platform agent"
          className="w-full rounded-xl border border-grid bg-surface px-4 py-2.5 text-sm text-ink outline-none focus:border-accent"
        />
        <button
          type="submit"
          disabled={send.isPending || !input.trim()}
          aria-label="Send"
          className="shrink-0 rounded-xl bg-accent px-4 py-2.5 text-white disabled:opacity-40"
        >
          <Send className="h-4 w-4" />
        </button>
      </form>
    </div>
  );
}

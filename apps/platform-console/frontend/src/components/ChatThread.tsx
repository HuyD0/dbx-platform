import { ArrowUp, DollarSign, Gauge, Shield, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { useChat } from "../lib/chat";
import type { Proposal } from "../lib/types";
import { ActionPlanDialog, PlanActionButton } from "./ActionPlanDialog";
import { ErrorState } from "./ui";

const SUGGESTIONS = [
  {
    icon: DollarSign,
    tint: "bg-series-1/15 icon-chip-1",
    title: "Find money leaks",
    prompt: "Where are we wasting the most money right now?",
  },
  {
    icon: Sparkles,
    tint: "bg-series-3/15 icon-chip-3",
    title: "Audit AI/ML",
    prompt: "Audit our serving endpoints and summarize the risks.",
  },
  {
    icon: Shield,
    tint: "bg-series-4/15 icon-chip-4",
    title: "Check security",
    prompt: "Any security findings I should worry about?",
  },
  {
    icon: Gauge,
    tint: "bg-series-2/15 icon-chip-2",
    title: "Clean up compute",
    prompt: "Clean up stale clusters.",
  },
];

function JobProposalCard({ proposal }: { proposal: Proposal }) {
  return (
    <div className="glass mt-2 flex flex-wrap items-center gap-2 rounded-xl px-3 py-2 text-xs">
      <span className="text-ink-2">
        Proposed job run: <span className="font-medium text-ink">{proposal.name}</span>
      </span>
      <PlanActionButton
        action="run-job"
        label="Review exact plan"
        parameters={{ job_id: proposal.job_id, job_name: proposal.name }}
        allowLegacy={false}
      />
    </div>
  );
}

function RunAllProposalCard({ proposal }: { proposal: Proposal }) {
  const runAll = useMutation({
    mutationFn: () => apiPost<RunAllResponse>("/api/jobs/run_all"),
  });
  return (
    <div className="glass mt-2 flex flex-wrap items-center gap-2 rounded-xl px-3 py-2 text-xs">
      <span className="text-ink-2">
        Run all{" "}
        <span className="font-medium text-ink">
          {typeof proposal.count === "number" ? proposal.count : ""} [dbx-platform] jobs
        </span>
        ?
      </span>
      {runAll.data ? (
        <>
          <Badge tone="good">started {runAll.data.count} runs</Badge>
          {runAll.data.failed.length > 0 && (
            <span title={runAll.data.failed.map((f) => `${f.name}: ${f.error}`).join("\n")}>
              <Badge tone="critical">{runAll.data.failed.length} failed</Badge>
            </span>
          )}
        </>
      ) : (
        <button
          type="button"
          onClick={() => runAll.mutate()}
          disabled={runAll.isPending}
          className="inline-flex items-center gap-1 rounded-lg border border-grid px-2.5 py-1 font-medium text-ink hover:bg-hairline disabled:opacity-50"
        >
          <Play className="h-3 w-3" />
          Run all jobs
        </button>
      )}
      {runAll.isError && <ErrorState error={runAll.error} />}
    </div>
  );
}

function ActionProposalCard({ proposal }: { proposal: Proposal }) {
  const [open, setOpen] = useState(false);
  const action = proposal.action ?? "";
  return (
    <div className="glass mt-2 flex flex-wrap items-center gap-2 rounded-xl px-3 py-2 text-xs">
      <span className="text-ink-2">
        Proposed: <span className="font-medium text-ink">{action}</span>
        {typeof proposal.count === "number" && ` — ${proposal.count} item(s) in the dry-run`}
      </span>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="rounded-lg border border-grid px-2.5 py-1 font-medium text-ink hover:bg-hairline"
      >
        Review &amp; plan
      </button>
      {open && (
        <ActionPlanDialog action={action} title={`Plan ${action}`} onClose={() => setOpen(false)} />
      )}
    </div>
  );
}

function Composer({ autoFocus }: { autoFocus?: boolean }) {
  const { send, pending } = useChat();
  const [input, setInput] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  const submit = () => {
    if (!input.trim() || pending) return;
    send(input);
    setInput("");
    if (ref.current) ref.current.style.height = "auto";
  };

  return (
    <div className="glass-strong glass-focus-accent rounded-3xl p-2.5 shadow-2xl shadow-black/10 transition-shadow focus-within:shadow-accent/10 focus-within:ring-2 focus-within:ring-accent/30 dark:shadow-black/40">
      <div className="flex items-end gap-2">
        <textarea
          ref={ref}
          value={input}
          autoFocus={autoFocus}
          rows={1}
          onChange={(e) => {
            setInput(e.target.value);
            e.target.style.height = "auto";
            e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder="Ask anything about your workspace…"
          aria-label="Message the platform agent"
          className="max-h-40 w-full resize-none bg-transparent px-3 py-2 text-[15px] text-ink outline-none placeholder:text-ink-2"
        />
        <button
          type="button"
          onClick={submit}
          disabled={pending || !input.trim()}
          aria-label="Send"
          className="shrink-0 rounded-full bg-accent p-2.5 text-white shadow-lg shadow-accent/30 transition-all hover:brightness-110 disabled:opacity-30 disabled:shadow-none"
        >
          <ArrowUp className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

/** The conversation itself, Claude-style: user turns as right-aligned soft
 * bubbles, assistant turns as plain prose behind a spark avatar, one shared
 * thread across surfaces. `compact` tightens spacing for the side panel. */
export function ChatThread({ compact = false }: { compact?: boolean }) {
  const { turns, pending, error, send } = useChat();
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, pending]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className={`flex-1 overflow-y-auto ${compact ? "px-3 py-3" : "px-1 py-4"}`}>
        {turns.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-6 text-center">
            <p
              className={`font-semibold tracking-tight text-ink ${
                compact ? "text-lg" : "text-3xl md:text-4xl"
              }`}
            >
              What should we look at?
            </p>
            <p className="max-w-md text-sm text-ink-2">
              I can investigate the page you are viewing and draft evidence-backed plans.
              Every change is revalidated and requires your exact-plan approval.
            </p>
            <div
              className={`grid w-full gap-3 ${
                compact ? "grid-cols-1 px-1" : "max-w-2xl grid-cols-2 md:grid-cols-4"
              }`}
            >
              {SUGGESTIONS.map(({ icon: Icon, tint, title, prompt }) => (
                <button
                  key={title}
                  type="button"
                  onClick={() => send(prompt)}
                  className={`glass glass-hover-accent group rounded-2xl text-left shadow-lg shadow-black/5 transition-all hover:-translate-y-0.5 hover:shadow-xl dark:shadow-black/20 ${
                    compact ? "flex items-center gap-3 p-3" : "p-4"
                  }`}
                >
                  <div className={`w-fit rounded-xl p-2 ${tint} ${compact ? "" : "mb-3"}`}>
                    <Icon className="h-4 w-4" />
                  </div>
                  <div>
                    <div className="text-sm font-medium text-ink">{title}</div>
                    <div className="mt-0.5 text-xs leading-snug text-ink-2">{prompt}</div>
                  </div>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className={`mx-auto w-full space-y-5 ${compact ? "" : "max-w-3xl"}`}>
            {turns.map((turn, i) =>
              turn.role === "user" ? (
                <div key={i} className="flex justify-end">
                  <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-accent/10 px-4 py-2.5 text-sm text-ink">
                    {turn.content}
                  </div>
                </div>
              ) : (
                <div key={i} className="flex gap-3">
                  <div className="mt-1 h-6 w-6 shrink-0 rounded-full bg-accent/15 p-1">
                    <Sparkles className="h-4 w-4 text-accent" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="prose-console text-ink-2">
                      <ReactMarkdown>{turn.content}</ReactMarkdown>
                    </div>
                    {turn.proposals?.map((p, j) =>
                      p.kind === "job" ? (
                        p.all ? (
                          <RunAllProposalCard key={j} proposal={p} />
                        ) : (
                          <JobProposalCard key={j} proposal={p} />
                        )
                      ) : (
                        <ActionProposalCard key={j} proposal={p} />
                      ),
                    )}
                  </div>
                </div>
              ),
            )}
            {pending && (
              <div className="flex gap-3">
                <div className="mt-1 h-6 w-6 shrink-0 animate-pulse rounded-full bg-accent/15 p-1">
                  <Sparkles className="h-4 w-4 text-accent" />
                </div>
                <div className="space-y-2 pt-1">
                  <div className="h-3 w-40 animate-pulse rounded bg-hairline" />
                  <div className="h-3 w-64 animate-pulse rounded bg-hairline" />
                </div>
              </div>
            )}
            {error != null && <ErrorState error={error} />}
            <div ref={bottom} />
          </div>
        )}
      </div>
      <div className={compact ? "border-t border-grid p-3" : "mx-auto w-full max-w-3xl pb-4"}>
        <Composer autoFocus={!compact} />
        <p className="mt-2 text-center text-[10px] text-muted">
          AI can investigate and propose. It cannot execute without a human-approved plan.
        </p>
      </div>
    </div>
  );
}

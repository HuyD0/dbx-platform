import {
  ArrowUp,
  BookOpenCheck,
  ChevronDown,
  Clock3,
  DollarSign,
  Gauge,
  Shield,
  Sparkles,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { useChat } from "../lib/chat";
import { timeAgo } from "../lib/format";
import type { AgentExecutionCategory, AgentExecutionTrace, Proposal } from "../lib/types";
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

const TRACE_CATEGORY: Record<AgentExecutionCategory, { label: string; bar: string; dot: string }> =
  {
    foundry_agent: {
      label: "Microsoft Foundry Agent tool calls",
      bar: "bg-[#FFCD67] text-[#240B15]",
      dot: "bg-[#FFCD67]",
    },
    databricks_retrieval: {
      label: "Databricks retrieval",
      bar: "bg-[#00AAAD] text-[#240B15]",
      dot: "bg-[#00AAAD]",
    },
    llm_synthesis: {
      label: "LLM synthesis",
      bar: "bg-[#8B001F] text-white",
      dot: "bg-[#8B001F]",
    },
  };

function isObservedDuration(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function formatDuration(value: number | null | undefined, perToken = false): string {
  if (!isObservedDuration(value)) return "Unavailable";
  if (perToken) return `${Number.isInteger(value) ? value : value.toFixed(1)} ms/token`;
  if (value >= 1000) return `${(value / 1000).toFixed(2)} s`;
  return `${Math.round(value)} ms`;
}

/** An expandable, keyboard-operable waterfall. It only plots durations supplied
 * by the server; absent telemetry is disclosed rather than estimated. */
function AgentExecutionFlamegraph({ trace }: { trace?: AgentExecutionTrace }) {
  const [expanded, setExpanded] = useState(false);
  const [selectedStageId, setSelectedStageId] = useState<string | null>(null);
  const regionId = useId();
  const stages = trace?.stages ?? [];
  const observedEnds = stages
    .filter((stage) => isObservedDuration(stage.duration_ms))
    .map((stage) => Math.max(0, stage.start_ms) + (stage.duration_ms ?? 0));
  const scaleMs = Math.max(
    isObservedDuration(trace?.total_ms) ? trace.total_ms : 0,
    ...observedEnds,
    1,
  );
  const hasServerTiming = trace?.timing_source === "server";
  const selectedStage = stages.find((stage) => stage.id === selectedStageId);

  return (
    <div className="mt-3 overflow-hidden rounded-xl border border-[#E4D7DB] bg-white">
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={regionId}
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left hover:bg-[#FBF7F8]"
      >
        <span className="flex min-w-0 items-center gap-2">
          <Clock3 className="h-3.5 w-3.5 shrink-0 text-[#8B001F]" />
          <span>
            <span className="block text-[11px] font-semibold text-[#240B15]">
              Agent execution flamegraph
            </span>
            <span className="block text-[10px] text-[#806A72]">
              {hasServerTiming
                ? `${stages.length} observed stage${stages.length === 1 ? "" : "s"} · ${formatDuration(trace?.total_ms)}`
                : "Timing telemetry unavailable"}
            </span>
          </span>
        </span>
        <ChevronDown
          aria-hidden="true"
          className={`h-4 w-4 shrink-0 text-[#8B001F] transition-transform ${
            expanded ? "rotate-180" : ""
          }`}
        />
      </button>

      {expanded && (
        <div id={regionId} className="border-t border-[#E4D7DB] p-3">
          <div className="grid grid-cols-2 gap-2" aria-label="Generation latency metrics">
            <div className="rounded-lg bg-[#FBF7F8] px-3 py-2">
              <p className="text-[10px] font-medium uppercase tracking-wide text-[#806A72]">
                Time to first token
              </p>
              <p className="mt-0.5 text-sm font-semibold tabular-nums text-[#240B15]">
                {formatDuration(trace?.ttft_ms)}
              </p>
            </div>
            <div className="rounded-lg bg-[#FBF7F8] px-3 py-2">
              <p className="text-[10px] font-medium uppercase tracking-wide text-[#806A72]">
                Time per output token
              </p>
              <p className="mt-0.5 text-sm font-semibold tabular-nums text-[#240B15]">
                {formatDuration(trace?.tpot_ms, true)}
              </p>
            </div>
          </div>

          {!hasServerTiming && (
            <p className="mt-3 rounded-lg border border-dashed border-[#E4D7DB] bg-[#FBF7F8] px-3 py-2 text-[11px] leading-5 text-[#806A72]">
              This response did not include server timing. Stage durations, TTFT, and TPOT are
              intentionally not estimated.
            </p>
          )}

          {stages.length > 0 && (
            <div className="mt-3">
              <div
                className="mb-1.5 flex justify-between text-[9px] tabular-nums text-[#B79AA3]"
                aria-hidden="true"
              >
                <span>0 ms</span>
                <span>{formatDuration(trace?.total_ms)}</span>
              </div>
              <ol className="space-y-2" aria-label="Agent execution stages">
                {stages.map((stage) => {
                  const category = TRACE_CATEGORY[stage.category];
                  const durationObserved = isObservedDuration(stage.duration_ms);
                  const left = Math.min(100, Math.max(0, (stage.start_ms / scaleMs) * 100));
                  const width = durationObserved
                    ? Math.max(4, Math.min(100 - left, ((stage.duration_ms ?? 0) / scaleMs) * 100))
                    : 100;
                  return (
                    <li key={stage.id}>
                      <p className="mb-1 flex items-center justify-between gap-2 text-[10px]">
                        <span className="truncate font-medium text-[#4B3F43]">
                          {category.label}
                        </span>
                        <span className="shrink-0 tabular-nums text-[#806A72]">
                          {formatDuration(stage.duration_ms)}
                        </span>
                      </p>
                      <div className="relative h-7 rounded-md bg-[#FBF7F8]">
                        <button
                          type="button"
                          aria-pressed={selectedStageId === stage.id}
                          aria-label={`${stage.label}, ${category.label}, ${formatDuration(stage.duration_ms)}`}
                          onClick={() =>
                            setSelectedStageId((current) =>
                              current === stage.id ? null : stage.id,
                            )
                          }
                          className={`absolute inset-y-0 overflow-hidden rounded-md px-2 text-left text-[10px] font-semibold shadow-sm ring-offset-1 ring-offset-white focus-visible:ring-2 focus-visible:ring-[#F00037] ${
                            durationObserved
                              ? category.bar
                              : "border border-dashed border-[#B79AA3] bg-white text-[#4B3F43]"
                          }`}
                          style={{ left: `${durationObserved ? left : 0}%`, width: `${width}%` }}
                        >
                          <span className="block truncate">{stage.label}</span>
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ol>
            </div>
          )}

          <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1" aria-label="Trace legend">
            {(Object.keys(TRACE_CATEGORY) as AgentExecutionCategory[]).map((key) => (
              <span key={key} className="inline-flex items-center gap-1 text-[9px] text-[#806A72]">
                <span className={`h-2 w-2 rounded-sm ${TRACE_CATEGORY[key].dot}`} />
                {TRACE_CATEGORY[key].label}
              </span>
            ))}
          </div>

          {selectedStage && (
            <div className="mt-3 rounded-lg border-l-2 border-[#8B001F] bg-[#F9EAED] px-3 py-2">
              <p className="text-[11px] font-semibold text-[#240B15]">{selectedStage.label}</p>
              <p className="mt-0.5 text-[10px] leading-4 text-[#4B3F43]">
                {selectedStage.detail || TRACE_CATEGORY[selectedStage.category].label}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function JobProposalCard({ proposal }: { proposal: Proposal }) {
  return (
    <div className="blueprint-proposal glass mt-2 flex flex-wrap items-center gap-2 rounded-xl px-3 py-2 text-xs">
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

function BatchJobProposalCard({ proposal }: { proposal: Proposal }) {
  return (
    <div className="blueprint-proposal glass mt-2 flex flex-wrap items-center gap-2 rounded-xl px-3 py-2 text-xs">
      <span className="text-ink-2">
        Batch proposal for{" "}
        <span className="font-medium text-ink">
          {typeof proposal.count === "number" ? proposal.count : ""} [dbx-platform] jobs
        </span>
        . Batch execution is unsupported; review and approve each exact job target separately.
      </span>
    </div>
  );
}

function ActionProposalCard({ proposal }: { proposal: Proposal }) {
  const [open, setOpen] = useState(false);
  const action = proposal.action ?? "";
  return (
    <div className="blueprint-proposal glass mt-2 flex flex-wrap items-center gap-2 rounded-xl px-3 py-2 text-xs">
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
              Investigate this workspace
            </p>
            <p className="max-w-md text-sm text-ink-2">
              I can investigate the page you are viewing and draft evidence-backed plans. Every
              change is revalidated and requires your exact-plan approval.
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
                  <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md border border-[#E4D7DB] bg-[#F9EAED] px-4 py-2.5 text-sm text-[#240B15]">
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
                    {(turn.citations?.length ?? 0) > 0 && (
                      <div className="mt-3 rounded-xl border border-grid bg-page/40 p-3">
                        <p className="flex items-center gap-1.5 text-[11px] font-semibold text-ink">
                          <BookOpenCheck className="h-3.5 w-3.5 text-accent" />
                          Sources cited
                        </p>
                        <ul className="mt-2 space-y-2" aria-label="Assistant evidence citations">
                          {turn.citations?.map((citation) => (
                            <li
                              key={citation.citation_id}
                              className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-[11px]"
                            >
                              <span className="font-medium text-ink-2">{citation.source}</span>
                              <span className="text-muted">
                                {citation.tool} ·{" "}
                                <time dateTime={citation.observed_at} title={citation.observed_at}>
                                  {timeAgo(citation.observed_at)}
                                </time>
                              </span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {turn.focusActionId && (turn.citations?.length ?? 0) === 0 && (
                      <p className="mt-3 rounded-lg border border-grid bg-page/40 px-3 py-2 text-[11px] text-muted">
                        No structured source citation was returned for this focused answer.
                      </p>
                    )}
                    {turn.proposals?.map((p, j) =>
                      p.kind === "job" ? (
                        p.all ? (
                          <BatchJobProposalCard key={j} proposal={p} />
                        ) : (
                          <JobProposalCard key={j} proposal={p} />
                        )
                      ) : (
                        <ActionProposalCard key={j} proposal={p} />
                      ),
                    )}
                    <AgentExecutionFlamegraph trace={turn.executionTrace} />
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

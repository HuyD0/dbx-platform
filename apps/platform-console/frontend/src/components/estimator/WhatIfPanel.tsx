import { useId } from "react";
import { compactNum } from "../../lib/format";
import { Card, HelpTip, SectionTitle } from "../ui";

/** The sizing levers the engine validates and that scale cost most directly. */
export interface WhatIfValues {
  monthly_requests: number;
  avg_input_tokens: number;
  avg_output_tokens: number;
}

function num(value: unknown, fallback: number): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function Slider({
  label,
  hint,
  value,
  min,
  max,
  step,
  display,
  onChange,
}: {
  label: string;
  hint: string;
  value: number;
  min: number;
  max: number;
  step: number;
  display: string;
  onChange: (value: number) => void;
}) {
  const id = useId();
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <label htmlFor={id} className="text-xs font-medium text-ink">
          {label}
        </label>
        <span className="rounded-full bg-series-1/10 px-2 py-0.5 text-xs font-semibold tabular-nums text-ink">
          {display}
        </span>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        aria-valuetext={display}
        className="mt-2 w-full accent-series-1"
      />
      <p className="mt-1 text-[11px] text-muted">{hint}</p>
    </div>
  );
}

/** Live what-if sliders shown on the results screen. Dragging updates the
 * requirements that feed the estimate query, so the numbers are the real
 * engine's recompute over the same priced snapshot — never a client-side
 * approximation. Edits here are not persisted; saving still recomputes and
 * stores server-side. Token values of 0 mean "use the pattern default". */
export function WhatIfPanel({
  values,
  onChange,
  recomputing = false,
}: {
  values: Record<string, unknown>;
  onChange: (patch: Partial<WhatIfValues>) => void;
  recomputing?: boolean;
}) {
  const requests = num(values.monthly_requests, 5000);
  const inputTokens = num(values.avg_input_tokens, 0);
  const outputTokens = num(values.avg_output_tokens, 0);
  const tokenDisplay = (n: number) =>
    n > 0 ? `${compactNum(n)} tok` : "pattern default";

  return (
    <Card>
      <SectionTitle
        title="What if the workload changes?"
        subtitle="Drag to re-estimate instantly — the engine recomputes over the same priced snapshot."
        right={
          <span className="inline-flex items-center gap-2">
            {recomputing && (
              <span className="text-[11px] text-muted" role="status">
                recomputing…
              </span>
            )}
            <HelpTip label="About what-if sliders">
              These move the sizing inputs and re-run the real estimate; they are
              not client-side guesses. Nothing here is saved — use “Save
              estimate” to persist a server-recomputed result.
            </HelpTip>
          </span>
        }
      />
      <div className="grid gap-4 sm:grid-cols-3">
        <Slider
          label="Sessions / month"
          hint="Total production sessions"
          value={requests}
          min={1000}
          max={Math.max(1_000_000, requests)}
          step={1000}
          display={`${compactNum(requests)} / mo`}
          onChange={(v) => onChange({ monthly_requests: v })}
        />
        <Slider
          label="Input tokens / session"
          hint="Prompt & retrieved context"
          value={inputTokens}
          min={0}
          max={Math.max(20_000, inputTokens)}
          step={100}
          display={tokenDisplay(inputTokens)}
          onChange={(v) => onChange({ avg_input_tokens: v })}
        />
        <Slider
          label="Output tokens / session"
          hint="Generated response"
          value={outputTokens}
          min={0}
          max={Math.max(8_000, outputTokens)}
          step={100}
          display={tokenDisplay(outputTokens)}
          onChange={(v) => onChange({ avg_output_tokens: v })}
        />
      </div>
    </Card>
  );
}

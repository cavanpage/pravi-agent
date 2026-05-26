import { useEffect, useMemo, useRef, useState } from "react";

import { RunEvent, subscribeRun } from "../lib/api";

type Props = {
  externalId: string;
  /** Optional spend cap from settings — drives the cost progress bar. */
  maxCostUsd?: number;
};

// Visual mapping for the event kinds the dev runner emits.
const KIND_STYLE: Record<string, string> = {
  run_started: "bg-blue-400/10 text-blue-300 ring-blue-400/20",
  run_finished: "bg-emerald-400/10 text-emerald-300 ring-emerald-400/20",
  assistant_text: "bg-white/5 text-neutral-300 ring-white/10",
  tool_use: "bg-indigo-400/10 text-indigo-300 ring-indigo-400/20",
  tool_result: "bg-white/5 text-neutral-400 ring-white/10",
  system: "bg-amber-400/10 text-amber-300 ring-amber-400/20",
  result: "bg-emerald-400/10 text-emerald-300 ring-emerald-400/20",
};

export function LiveRunPanel({ externalId, maxCostUsd }: Props) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setEvents([]);
    setError(null);
    setConnected(true);
    const off = subscribeRun(externalId, {
      onEvent: (e) =>
        // Dedupe by id — the SSE endpoint already does this server-side
        // but be defensive against reconnects double-delivering.
        setEvents((prev) => (prev.some((p) => p.id === e.id) ? prev : [...prev, e])),
      onClose: () => setConnected(false),
      onError: (msg) => setError(msg),
    });
    return () => {
      off();
      setConnected(false);
    };
  }, [externalId]);

  // Derived metrics from the stream — cheap to recompute on each render.
  const metrics = useMemo(() => {
    const turns = events.filter((e) => e.kind === "tool_use" || e.kind === "assistant_text").length;
    const toolUses = events.filter((e) => e.kind === "tool_use").length;
    const finished = events.find((e) => e.kind === "run_finished");
    const result = events.find((e) => e.kind === "result");
    const finalCost =
      ((finished?.payload?.total_cost_usd as number | null | undefined) ??
        (result?.payload?.total_cost_usd as number | null | undefined)) ??
      null;
    const finalTurns =
      ((finished?.payload?.num_turns as number | undefined) ??
        (result?.payload?.num_turns as number | undefined)) ??
      turns;
    const started = events.find((e) => e.kind === "run_started")?.at;
    const ended = finished?.at;
    const elapsedMs = started
      ? (ended ? new Date(ended).getTime() : Date.now()) - new Date(started).getTime()
      : 0;
    const lastToolUse = [...events].reverse().find((e) => e.kind === "tool_use");
    return {
      turns: finalTurns,
      toolUses,
      cost: finalCost,
      elapsedMs,
      finished: !!finished,
      currentTool: lastToolUse?.message ?? null,
    };
  }, [events]);

  // Keep the tail in view as new events arrive.
  const tailRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    tailRef.current?.scrollTo({ top: tailRef.current.scrollHeight });
  }, [events.length]);

  const costPct =
    maxCostUsd && metrics.cost != null
      ? Math.min(100, (metrics.cost / maxCostUsd) * 100)
      : null;

  return (
    <section className="rounded-2xl border border-white/10 bg-white/[0.02] overflow-hidden">
      <header className="flex items-center justify-between gap-4 px-4 py-3 border-b border-white/10">
        <div className="flex items-center gap-2">
          <span
            className={`size-1.5 rounded-full ${
              metrics.finished
                ? "bg-emerald-400"
                : connected
                  ? "bg-blue-400 animate-pulse"
                  : "bg-neutral-600"
            }`}
          />
          <h3 className="text-[11px] uppercase tracking-[0.14em] font-semibold text-neutral-400">
            live run
          </h3>
          {metrics.finished ? (
            <span className="text-[11px] text-emerald-400">finished</span>
          ) : connected ? (
            <span className="text-[11px] text-neutral-500">streaming</span>
          ) : (
            <span className="text-[11px] text-neutral-600">idle</span>
          )}
        </div>
        {error ? (
          <span className="text-[11px] text-rose-400 font-mono">{error}</span>
        ) : null}
      </header>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-white/5">
        <Stat label="turns" value={String(metrics.turns)} />
        <Stat label="tools" value={String(metrics.toolUses)} />
        <Stat label="elapsed" value={formatDuration(metrics.elapsedMs)} />
        <Stat
          label="cost"
          value={metrics.cost != null ? `$${metrics.cost.toFixed(4)}` : "—"}
          sub={
            costPct != null ? (
              <div className="h-1 rounded-full bg-white/10 overflow-hidden mt-1.5">
                <div
                  className={`h-full transition-all ${
                    costPct > 80 ? "bg-rose-400" : costPct > 50 ? "bg-amber-400" : "bg-blue-400"
                  }`}
                  style={{ width: `${costPct}%` }}
                />
              </div>
            ) : null
          }
        />
      </div>

      {metrics.currentTool && !metrics.finished ? (
        <div className="px-4 py-2 text-xs text-neutral-400 border-b border-white/10 font-mono flex items-center gap-2">
          <span className="text-neutral-600">current →</span>
          <span className="text-indigo-300">{metrics.currentTool}</span>
        </div>
      ) : null}

      <div
        ref={tailRef}
        className="max-h-[360px] overflow-y-auto px-4 py-3 flex flex-col gap-1.5"
      >
        {events.length === 0 ? (
          <p className="text-sm text-neutral-600 italic">
            waiting for the dev agent to start…
          </p>
        ) : (
          events.map((e) => <EventRow key={e.id} event={e} />)
        )}
      </div>
    </section>
  );
}

function Stat({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: React.ReactNode;
}) {
  return (
    <div className="bg-neutral-950 px-4 py-3">
      <div className="text-[10px] uppercase tracking-[0.14em] text-neutral-500 font-semibold">
        {label}
      </div>
      <div className="text-lg font-semibold tracking-tight tabular-nums mt-0.5">
        {value}
      </div>
      {sub}
    </div>
  );
}

function EventRow({ event }: { event: RunEvent }) {
  const style = KIND_STYLE[event.kind] || "bg-white/5 text-neutral-400 ring-white/10";
  return (
    <div className="flex items-start gap-2 text-sm">
      <span
        className={`shrink-0 inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-mono tracking-wide ring-1 ring-inset ${style}`}
      >
        {event.kind}
      </span>
      <span className="text-neutral-300 leading-relaxed font-mono text-[12px] whitespace-pre-wrap break-words flex-1 min-w-0">
        {event.message}
      </span>
    </div>
  );
}

function formatDuration(ms: number): string {
  if (ms <= 0) return "0s";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  return `${m}m ${rs}s`;
}

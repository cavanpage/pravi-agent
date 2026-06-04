import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { RunEvent, subscribeSubtreeRun } from "../lib/api";

/** Cross-task aggregated live feed for an epic or feature ticket page.
 *
 * Subscribes to the subtree SSE stream which multiplexes per-task NOTIFY
 * channels into one feed; each event arrives tagged with which task
 * emitted it. Rolling buffer of the last `bufferSize` events; new events
 * push older ones out. Newest at the top so the eye lands on what just
 * happened. */

const KIND_TONE: Record<string, string> = {
  run_started: "text-blue-300",
  run_finished: "text-emerald-300",
  assistant_text: "text-neutral-300",
  tool_use: "text-indigo-300",
  tool_result: "text-neutral-400",
  system: "text-amber-300",
  result: "text-emerald-300",
};

const KIND_LABEL: Record<string, string> = {
  run_started: "started",
  run_finished: "finished",
  assistant_text: "text",
  tool_use: "tool",
  tool_result: "result",
  system: "system",
  result: "result",
};

export function SubtreeActivityPanel({
  externalId,
  parentKind,
  bufferSize = 100,
}: {
  externalId: string;
  parentKind: "epic" | "feature";
  bufferSize?: number;
}) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    setEvents([]);
    setError(null);
    setConnected(true);
    const off = subscribeSubtreeRun(
      externalId,
      {
        onEvent: (e) =>
          setEvents((prev) => {
            if (prev.some((p) => p.id === e.id)) return prev;
            // Append (chronological) then drop from the front if we're
            // past the buffer. Cheap for the ~100-element bound.
            const next = [...prev, e];
            return next.length > bufferSize
              ? next.slice(next.length - bufferSize)
              : next;
          }),
        onClose: () => setConnected(false),
        onError: (msg) => setError(msg),
      },
      { replay: bufferSize },
    );
    return () => {
      off();
      setConnected(false);
    };
  }, [externalId, bufferSize]);

  const metrics = useMemo(() => {
    const taskIds = new Set<number>();
    const activeTaskIds = new Set<number>();
    let toolUses = 0;
    let totalCost = 0;
    for (const e of events) {
      taskIds.add(e.ticket_id);
      if (e.kind === "tool_use") toolUses++;
      if (e.kind === "run_started") activeTaskIds.add(e.ticket_id);
      if (e.kind === "run_finished") {
        activeTaskIds.delete(e.ticket_id);
        const c = e.payload?.total_cost_usd;
        if (typeof c === "number") totalCost += c;
      }
    }
    return {
      taskCount: taskIds.size,
      activeCount: activeTaskIds.size,
      toolUses,
      totalCost,
    };
  }, [events]);

  // Auto-scroll to bottom (newest) when new events arrive AND the user
  // is already near the bottom — otherwise let them keep their scroll
  // position so they can read history.
  const listRef = useRef<HTMLDivElement>(null);
  const atBottomRef = useRef(true);
  useEffect(() => {
    const el = listRef.current;
    if (!el || !atBottomRef.current) return;
    el.scrollTo({ top: el.scrollHeight });
  }, [events.length]);
  const onScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    atBottomRef.current = el.scrollTop + el.clientHeight + 8 >= el.scrollHeight;
  };

  return (
    <section className="mt-8 rounded-2xl border border-white/10 bg-white/[0.02] flex flex-col">
      <header className="flex items-center justify-between gap-3 flex-wrap p-4 pb-3 border-b border-white/5">
        <div className="flex items-center gap-2">
          <h2 className="text-[11px] uppercase tracking-[0.14em] font-semibold text-neutral-500">
            agent activity
          </h2>
          <span className="text-xs text-neutral-500">
            across this {parentKind}'s tasks
          </span>
          {connected ? (
            <span className="inline-flex items-center gap-1.5 text-[10px] text-emerald-300">
              <span className="size-1.5 rounded-full bg-emerald-300 animate-pulse" />
              live
            </span>
          ) : (
            <span className="text-[10px] text-neutral-500">disconnected</span>
          )}
        </div>
        <div className="flex items-center gap-3 text-[11px] text-neutral-500 font-mono tabular-nums">
          {metrics.activeCount > 0 ? (
            <span className="text-blue-300">
              {metrics.activeCount} running
            </span>
          ) : null}
          <span>{metrics.taskCount} task{metrics.taskCount === 1 ? "" : "s"}</span>
          <span>{metrics.toolUses} tool uses</span>
          {metrics.totalCost > 0 ? (
            <span>${metrics.totalCost.toFixed(2)} spent</span>
          ) : null}
        </div>
      </header>

      {error ? (
        <div className="px-4 py-2 text-xs text-rose-300 border-b border-white/5">
          {error}
        </div>
      ) : null}

      <div
        ref={listRef}
        onScroll={onScroll}
        className="max-h-[420px] overflow-y-auto px-4 py-3 flex flex-col gap-1"
      >
        {events.length === 0 ? (
          <p className="text-xs text-neutral-500 italic py-6 text-center">
            no agent activity yet — start some tasks to see this feed light up.
          </p>
        ) : (
          events.map((e) => <ActivityRow key={e.id} event={e} />)
        )}
      </div>
    </section>
  );
}

function ActivityRow({ event }: { event: RunEvent }) {
  const tone = KIND_TONE[event.kind] ?? "text-neutral-400";
  const label = KIND_LABEL[event.kind] ?? event.kind;
  const time = new Date(event.at).toLocaleTimeString([], {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

  // Pick a compact summary depending on the kind. Most useful: the
  // file/command names for tool_use; the message for run lifecycle events.
  const summary = useMemo(() => summarize(event), [event]);

  return (
    <div className="grid grid-cols-[auto_auto_1fr_auto] gap-2 items-baseline text-xs font-mono tabular-nums leading-relaxed">
      <span className="text-neutral-600">{time}</span>
      <span className={`shrink-0 ${tone}`}>{label}</span>
      <div className="min-w-0 truncate text-neutral-300" title={summary}>
        {summary}
      </div>
      {event.ticket_external_id ? (
        <Link
          to={`/tickets/${encodeURIComponent(event.ticket_external_id)}`}
          className="shrink-0 text-[10px] text-neutral-500 hover:text-neutral-300 transition truncate max-w-[140px]"
          title={event.ticket_title ?? event.ticket_external_id}
        >
          {event.ticket_external_id}
        </Link>
      ) : null}
    </div>
  );
}

function summarize(event: RunEvent): string {
  const p = event.payload ?? {};
  if (event.kind === "tool_use") {
    const tool = (p.tool_name as string | undefined) ?? "tool";
    const input = (p.tool_input as Record<string, unknown> | undefined) ?? {};
    // Common single-string args: file_path, command, pattern, url.
    const arg =
      (input.file_path as string | undefined) ??
      (input.command as string | undefined) ??
      (input.pattern as string | undefined) ??
      (input.url as string | undefined) ??
      "";
    return arg ? `${tool}: ${arg}` : tool;
  }
  if (event.kind === "run_started") return "dev agent started";
  if (event.kind === "run_finished") {
    const turns = p.num_turns ?? "?";
    const cost = typeof p.total_cost_usd === "number"
      ? `$${(p.total_cost_usd as number).toFixed(2)}`
      : "?";
    return `done · ${turns} turns · ${cost}`;
  }
  return event.message || event.kind;
}

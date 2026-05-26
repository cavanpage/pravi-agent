import { useMemo } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { api, RunRow } from "../lib/api";

const STATUS_STYLE: Record<string, string> = {
  started: "bg-blue-400/10 text-blue-300 ring-blue-400/20",
  succeeded: "bg-emerald-400/10 text-emerald-300 ring-emerald-400/20",
  failed: "bg-rose-400/10 text-rose-300 ring-rose-400/20",
  budget_exhausted: "bg-amber-400/10 text-amber-300 ring-amber-400/20",
};

export function RunsPage() {
  const runsQ = useQuery({
    queryKey: ["runs"],
    queryFn: () => api.listRuns(200),
    // Keep in-flight runs ticking — cheap query, only top-100 rows.
    refetchInterval: 5_000,
  });

  const runs = runsQ.data ?? [];
  const summary = useMemo(() => deriveSummary(runs), [runs]);

  return (
    <div className="max-w-6xl mx-auto px-6 sm:px-8 py-10">
      <header className="flex items-center justify-between">
        <div>
          <Link to="/" className="text-xs text-neutral-500 hover:text-neutral-300 transition">
            ← home
          </Link>
          <h1 className="text-3xl font-semibold tracking-tight mt-2">runs</h1>
          <p className="text-neutral-500 text-sm mt-1">
            Every agent run across all tickets. Sorted newest first.
          </p>
        </div>
        {runsQ.isFetching ? (
          <span className="text-[11px] text-neutral-500 flex items-center gap-1.5">
            <span className="size-1.5 rounded-full bg-blue-400 animate-pulse" />
            refreshing
          </span>
        ) : null}
      </header>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-8">
        <SummaryCard label="total runs" value={String(summary.total)} />
        <SummaryCard
          label="success rate"
          value={summary.total > 0 ? `${Math.round((summary.succeeded / summary.total) * 100)}%` : "—"}
        />
        <SummaryCard label="total spend" value={`$${summary.totalCost.toFixed(2)}`} />
        <SummaryCard
          label="avg duration"
          value={summary.avgDurationMs ? formatDuration(summary.avgDurationMs) : "—"}
        />
      </div>

      <div className="mt-8 rounded-2xl border border-white/10 bg-white/[0.02] overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-[10px] uppercase tracking-[0.14em] text-neutral-500 font-semibold">
              <Th>started</Th>
              <Th>ticket</Th>
              <Th>kind</Th>
              <Th>status</Th>
              <Th align="right">turns</Th>
              <Th align="right">duration</Th>
              <Th align="right">cost</Th>
            </tr>
          </thead>
          <tbody>
            {runsQ.isLoading ? (
              <tr>
                <td colSpan={7} className="py-8 text-center text-neutral-500 text-sm">
                  loading…
                </td>
              </tr>
            ) : runs.length === 0 ? (
              <tr>
                <td colSpan={7} className="py-8 text-center text-neutral-600 text-sm italic">
                  no runs yet — approve a plan on a ticket and the dev agent
                  will show up here.
                </td>
              </tr>
            ) : (
              runs.map((r) => <RunRowView key={r.id} run={r} />)
            )}
          </tbody>
        </table>
      </div>

      {runsQ.error ? (
        <p className="text-xs text-rose-400 mt-3">{(runsQ.error as Error).message}</p>
      ) : null}
    </div>
  );
}

function RunRowView({ run }: { run: RunRow }) {
  const style = STATUS_STYLE[run.status] || "bg-white/5 text-neutral-400 ring-white/10";
  const inFlight = run.status === "started";
  const duration = run.duration_ms
    ? formatDuration(run.duration_ms)
    : inFlight
      ? formatDuration(Date.now() - new Date(run.started_at).getTime())
      : "—";
  return (
    <tr className="border-t border-white/5 hover:bg-white/[0.03] transition">
      <Td>
        <span title={new Date(run.started_at).toLocaleString()} className="text-neutral-400">
          {formatRelative(run.started_at)}
        </span>
      </Td>
      <Td>
        <Link
          to={`/tickets/${encodeURIComponent(run.ticket_external_id)}`}
          className="block min-w-0"
        >
          <div className="text-neutral-100 truncate max-w-[28ch]">{run.ticket_title}</div>
          <div className="text-[11px] text-neutral-500 font-mono truncate">
            {run.ticket_external_id} · {run.repo_name}
          </div>
        </Link>
      </Td>
      <Td>
        <span className="text-neutral-400 font-mono text-[12px]">{run.kind}</span>
      </Td>
      <Td>
        <span
          className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-mono tracking-wide ring-1 ring-inset ${style}`}
        >
          {inFlight ? (
            <span className="size-1.5 rounded-full bg-current animate-pulse" />
          ) : (
            <span className="size-1.5 rounded-full bg-current opacity-80" />
          )}
          {run.status}
        </span>
      </Td>
      <Td align="right">
        <span className="tabular-nums text-neutral-300">
          {run.num_turns ?? (inFlight ? "…" : "—")}
        </span>
      </Td>
      <Td align="right">
        <span className="tabular-nums text-neutral-300">{duration}</span>
      </Td>
      <Td align="right">
        <span className="tabular-nums text-neutral-300">
          {run.total_cost_usd != null ? `$${run.total_cost_usd.toFixed(4)}` : "—"}
        </span>
      </Td>
    </tr>
  );
}

function SummaryCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.02] px-4 py-3">
      <div className="text-[10px] uppercase tracking-[0.14em] text-neutral-500 font-semibold">
        {label}
      </div>
      <div className="text-2xl font-semibold tracking-tight tabular-nums mt-1">{value}</div>
    </div>
  );
}

function Th({ children, align }: { children: React.ReactNode; align?: "right" }) {
  return (
    <th
      className={`px-4 py-3 font-semibold ${align === "right" ? "text-right" : "text-left"}`}
    >
      {children}
    </th>
  );
}

function Td({ children, align }: { children: React.ReactNode; align?: "right" }) {
  return (
    <td className={`px-4 py-3 align-top ${align === "right" ? "text-right" : "text-left"}`}>
      {children}
    </td>
  );
}

// Same logic as LiveRunPanel — kept here to avoid a shared util file for one helper.
function formatDuration(ms: number): string {
  if (ms <= 0) return "0s";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  if (m < 60) return `${m}m ${rs}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  const diff = Date.now() - then;
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

function deriveSummary(runs: RunRow[]) {
  let succeeded = 0;
  let totalCost = 0;
  let durationSum = 0;
  let durationCount = 0;
  for (const r of runs) {
    if (r.status === "succeeded") succeeded += 1;
    if (r.total_cost_usd != null) totalCost += r.total_cost_usd;
    if (r.duration_ms != null) {
      durationSum += r.duration_ms;
      durationCount += 1;
    }
  }
  return {
    total: runs.length,
    succeeded,
    totalCost,
    avgDurationMs: durationCount > 0 ? Math.round(durationSum / durationCount) : 0,
  };
}

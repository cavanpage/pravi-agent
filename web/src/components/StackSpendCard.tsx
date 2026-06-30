import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api, Stack, StackSpend, SpendWindow } from "../lib/api";

/** Per-stack FinOps widget — the second axis to PersonaSpendCard (ADR 0004
 * § FinOps slice). Same compact card, same window chips; consumes
 * `/api/spend/by-stack`. Empty state suppresses the card so a fresh install
 * doesn't see "0 stacks spent". */

const WINDOWS: { slug: SpendWindow; label: string }[] = [
  { slug: "7d", label: "7d" },
  { slug: "30d", label: "30d" },
  { slug: "all", label: "All-time" },
];

// Stacks are an open set with no persona-style `group`, so colour them
// deterministically from a fixed palette keyed by slug. Stable across
// renders (same slug → same tone) without needing a server-side mapping.
const STACK_BAR = [
  "bg-sky-400/70",
  "bg-violet-400/70",
  "bg-teal-400/70",
  "bg-rose-400/70",
  "bg-amber-400/70",
  "bg-indigo-400/70",
  "bg-lime-400/70",
  "bg-pink-400/70",
];
const UNKNOWN_BAR = "bg-neutral-500/70";

function toneFor(slug: string): string {
  if (slug === "unknown") return UNKNOWN_BAR;
  // Cheap stable hash → palette index.
  let h = 0;
  for (let i = 0; i < slug.length; i++) h = (h * 31 + slug.charCodeAt(i)) >>> 0;
  return STACK_BAR[h % STACK_BAR.length];
}

export function StackSpendCard({
  stackCatalog,
}: {
  stackCatalog: Stack[] | null;
}) {
  const [windowSel, setWindowSel] = useState<SpendWindow>("30d");
  const spendQ = useQuery({
    queryKey: ["spend-by-stack", windowSel],
    queryFn: () => api.spendByStack(windowSel),
    staleTime: 30_000,
  });

  const rows = spendQ.data ?? [];
  const total = rows.reduce((n, r) => n + r.spent_usd, 0);

  // Don't render at all on a fresh install — mirrors PersonaSpendCard.
  if (!spendQ.isLoading && total === 0) return null;

  return (
    <section className="mt-8 rounded-2xl border border-white/10 bg-white/[0.02] p-4">
      <header className="flex items-center justify-between gap-3 flex-wrap mb-3">
        <h2 className="text-[11px] uppercase tracking-[0.14em] font-semibold text-neutral-500">
          Spend by stack
          <span className="text-neutral-600 normal-case tracking-normal font-normal ml-2 tabular-nums">
            ${total.toFixed(2)}
          </span>
        </h2>
        <div className="inline-flex rounded-full border border-white/10 bg-white/[0.02] p-0.5">
          {WINDOWS.map((w) => {
            const active = w.slug === windowSel;
            return (
              <button
                key={w.slug}
                type="button"
                onClick={() => setWindowSel(w.slug)}
                className={`px-3 py-1 text-xs rounded-full transition ${
                  active
                    ? "bg-white/10 text-neutral-100"
                    : "text-neutral-500 hover:text-neutral-200"
                }`}
              >
                {w.label}
              </button>
            );
          })}
        </div>
      </header>

      {spendQ.isLoading ? (
        <p className="text-xs text-neutral-500 italic">loading…</p>
      ) : (
        <>
          {/* Stacked horizontal bar — at-a-glance share-of-spend. */}
          <div className="flex h-2 w-full rounded-full overflow-hidden bg-white/[0.04] mb-3">
            {rows.map((r) => {
              const pct = total > 0 ? (r.spent_usd / total) * 100 : 0;
              if (pct < 0.5) return null;
              return (
                <div
                  key={r.stack}
                  className={toneFor(r.stack)}
                  style={{ width: `${pct}%` }}
                  title={`${r.stack}: $${r.spent_usd.toFixed(2)} (${pct.toFixed(1)}%)`}
                />
              );
            })}
          </div>

          {/* Per-row table. Most-spending first (server already sorts). */}
          <ul className="flex flex-col gap-1">
            {rows.map((r) => (
              <SpendRow key={r.stack} row={r} catalog={stackCatalog} total={total} />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function SpendRow({
  row,
  catalog,
  total,
}: {
  row: StackSpend;
  catalog: Stack[] | null;
  total: number;
}) {
  const entry = catalog?.find((s) => s.slug === row.stack);
  const name = entry?.name ?? row.stack;
  const pct = total > 0 ? (row.spent_usd / total) * 100 : 0;
  const tone = toneFor(row.stack);

  return (
    <li className="flex items-center gap-3 text-xs py-1">
      <span className={`size-2 rounded-full ${tone}`} aria-hidden />
      <span className="flex-1 truncate text-neutral-200" title={row.stack}>
        {name}
      </span>
      <span className="text-neutral-500 tabular-nums">
        {row.run_count} run{row.run_count === 1 ? "" : "s"} · {row.ticket_count}{" "}
        ticket{row.ticket_count === 1 ? "" : "s"}
      </span>
      <span className="text-neutral-100 tabular-nums font-mono w-20 text-right">
        ${row.spent_usd.toFixed(2)}
      </span>
      <span className="text-neutral-500 tabular-nums w-12 text-right">
        {pct.toFixed(0)}%
      </span>
    </li>
  );
}

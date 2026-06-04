import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api, Persona, PersonaSpend, SpendWindow } from "../lib/api";

/** Per-persona FinOps widget — see ADR 0004 § FinOps slice. Compact card
 * for the home dashboard; clicking a window chip re-queries. Empty state
 * suppresses the card so a fresh install doesn't see "0 personas spent". */

const WINDOWS: { slug: SpendWindow; label: string }[] = [
  { slug: "7d", label: "7d" },
  { slug: "30d", label: "30d" },
  { slug: "all", label: "All-time" },
];

// Persona-group → coloured bar segment. Matches PersonaChip tones.
const GROUP_BAR: Record<string, string> = {
  product: "bg-fuchsia-400/70",
  architecture: "bg-purple-400/70",
  engineering: "bg-blue-400/70",
  quality: "bg-emerald-400/70",
  platform: "bg-amber-400/70",
  other: "bg-neutral-500/70",
};

export function PersonaSpendCard({
  personaCatalog,
}: {
  personaCatalog: Persona[] | null;
}) {
  const [windowSel, setWindowSel] = useState<SpendWindow>("30d");
  const spendQ = useQuery({
    queryKey: ["spend-by-persona", windowSel],
    queryFn: () => api.spendByPersona(windowSel),
    staleTime: 30_000,
  });

  const rows = spendQ.data ?? [];
  const total = rows.reduce((n, r) => n + r.spent_usd, 0);

  // Don't render at all on a fresh install — the dashboard already shows
  // an empty-state for tickets; a "0 spend" card adds noise.
  if (!spendQ.isLoading && total === 0) return null;

  return (
    <section className="mt-8 rounded-2xl border border-white/10 bg-white/[0.02] p-4">
      <header className="flex items-center justify-between gap-3 flex-wrap mb-3">
        <h2 className="text-[11px] uppercase tracking-[0.14em] font-semibold text-neutral-500">
          Spend by persona
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
              const group = personaCatalog?.find((p) => p.slug === r.persona)?.group ?? "other";
              const tone = GROUP_BAR[group] ?? GROUP_BAR.other;
              return (
                <div
                  key={r.persona}
                  className={tone}
                  style={{ width: `${pct}%` }}
                  title={`${r.persona}: $${r.spent_usd.toFixed(2)} (${pct.toFixed(1)}%)`}
                />
              );
            })}
          </div>

          {/* Per-row table. Most-spending first (server already sorts). */}
          <ul className="flex flex-col gap-1">
            {rows.map((r) => (
              <SpendRow key={r.persona} row={r} catalog={personaCatalog} total={total} />
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
  row: PersonaSpend;
  catalog: Persona[] | null;
  total: number;
}) {
  const entry = catalog?.find((p) => p.slug === row.persona);
  const name = entry?.name ?? row.persona;
  const group = entry?.group ?? "other";
  const pct = total > 0 ? (row.spent_usd / total) * 100 : 0;
  const tone = GROUP_BAR[group] ?? GROUP_BAR.other;

  return (
    <li className="flex items-center gap-3 text-xs py-1">
      <span className={`size-2 rounded-full ${tone}`} aria-hidden />
      <span className="flex-1 truncate text-neutral-200" title={entry?.description}>
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

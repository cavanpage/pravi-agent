import { useQuery } from "@tanstack/react-query";

import { api, CostRollup, Ticket } from "../lib/api";

/**
 * Consolidates the three "context" blocks that used to take three full-width
 * sections (details metadata, budget meter, description) into one compact
 * card. Keeps the rest of the page focused on action surfaces.
 */
export function TicketInfoCard({ ticket }: { ticket: Ticket }) {
  return (
    <section className="rounded-2xl border border-white/10 bg-white/[0.02] overflow-hidden">
      <div className="grid grid-cols-1 md:grid-cols-2">
        <DetailsBlock ticket={ticket} />
        <BudgetBlock externalId={ticket.external_id} />
      </div>
      {ticket.body ? <DescriptionBlock body={ticket.body} /> : null}
    </section>
  );
}

function DetailsBlock({ ticket }: { ticket: Ticket }) {
  return (
    <div className="px-4 py-3 md:border-r border-white/10">
      <div className="text-[10px] uppercase tracking-[0.14em] text-neutral-500 font-semibold mb-2">
        details
      </div>
      <dl className="text-xs grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 font-mono">
        <Row label="id" value={ticket.external_id} />
        <Row label="repo" value={ticket.repo.name} />
        <Row label="domain" value={ticket.domain_name || "—"} />
        {ticket.parent_external_id ? (
          <Row label="parent" value={ticket.parent_external_id} />
        ) : null}
        {ticket.child_count > 0 ? (
          <Row
            label="children"
            value={`${ticket.child_count} ${ticket.child_count === 1 ? "" : ""}`.trim()}
            valueText={`${ticket.child_count}`}
          />
        ) : null}
      </dl>
    </div>
  );
}

function Row({
  label,
  value,
  valueText,
}: {
  label: string;
  value: string;
  valueText?: string;
}) {
  return (
    <>
      <dt className="text-neutral-500">{label}</dt>
      <dd className="text-neutral-200 truncate" title={value}>
        {valueText ?? value}
      </dd>
    </>
  );
}

function BudgetBlock({ externalId }: { externalId: string }) {
  const q = useQuery({
    queryKey: ["cost-rollup", externalId],
    queryFn: () => api.costRollup(externalId),
    refetchInterval: 10_000,
  });

  return (
    <div className="px-4 py-3">
      <div className="flex items-baseline justify-between mb-2">
        <span className="text-[10px] uppercase tracking-[0.14em] text-neutral-500 font-semibold">
          budget
        </span>
        {q.data ? (
          <span className="text-[10px] text-neutral-600 font-mono">
            cap: {q.data.constraint_source}
          </span>
        ) : null}
      </div>
      {q.isLoading || !q.data ? (
        <div className="text-xs text-neutral-500 italic">loading…</div>
      ) : (
        <BudgetSummary rollup={q.data} />
      )}
    </div>
  );
}

function BudgetSummary({ rollup }: { rollup: CostRollup }) {
  const spent = rollup.own_spent_usd;
  // The "ceiling" for the meter is the effective constraint — own ceiling if
  // set, otherwise inherited remaining + spent. We display that combined cap
  // so the bar reflects what's actually enforced.
  const cap =
    rollup.effective_remaining_usd !== null
      ? spent + rollup.effective_remaining_usd
      : null;
  const pct =
    cap != null && cap > 0 ? Math.min(100, Math.round((spent / cap) * 100)) : 0;
  const colour =
    pct >= 90
      ? "bg-rose-400"
      : pct >= 70
        ? "bg-amber-400"
        : "bg-emerald-400";

  return (
    <div>
      <div className="flex items-baseline gap-2">
        <span className="text-xl font-semibold tabular-nums">
          ${spent.toFixed(2)}
        </span>
        {cap != null ? (
          <span className="text-xs text-neutral-500 tabular-nums">
            / ${cap.toFixed(2)}
          </span>
        ) : (
          <span className="text-xs text-neutral-600 italic">unlimited</span>
        )}
        {cap != null ? (
          <span
            className={`text-[10px] tabular-nums ml-auto ${
              pct >= 90 ? "text-rose-300" : pct >= 70 ? "text-amber-300" : "text-neutral-500"
            }`}
          >
            {pct}%
          </span>
        ) : null}
      </div>
      {cap != null ? (
        <div className="h-1 rounded-full bg-white/10 overflow-hidden mt-2">
          <div className={`h-full ${colour} transition-all`} style={{ width: `${pct}%` }} />
        </div>
      ) : null}
    </div>
  );
}

function DescriptionBlock({ body }: { body: string }) {
  return (
    <details className="border-t border-white/10 group">
      <summary className="cursor-pointer list-none px-4 py-2.5 flex items-center gap-2 text-sm text-neutral-300 hover:text-neutral-100 transition select-none">
        <span className="inline-block transition-transform group-open:rotate-90 text-neutral-500">
          ›
        </span>
        <span className="text-[10px] uppercase tracking-[0.14em] font-semibold text-neutral-500">
          description
        </span>
      </summary>
      <pre className="whitespace-pre-wrap px-4 pb-4 text-sm text-neutral-300 font-mono">
        {body}
      </pre>
    </details>
  );
}

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, BudgetBreakdown } from "../lib/api";

type Props = {
  externalId: string;
  /** Hide the "edit ceiling" affordance when the user can't act here. */
  readOnly?: boolean;
};

/**
 * Per-ticket budget panel: own spend vs ceiling, plus a per-ancestor
 * breakdown when something up the chain is the binding constraint.
 *
 * Fetches once on mount and re-fetches when the ceiling is edited (via
 * the queryClient.invalidateQueries call inside EditCeilingButton's
 * onSaved callback). No polling — the cost endpoint walks the subtree on
 * every call, so leaving it on a 5s timer was wasteful when the spend
 * only meaningfully changes at run_finished.
 */
export function BudgetMeter({ externalId, readOnly }: Props) {
  const qc = useQueryClient();
  const rollupQ = useQuery({
    queryKey: ["cost-rollup", externalId],
    queryFn: () => api.costRollup(externalId),
  });

  if (rollupQ.isLoading) {
    return (
      <section className="rounded-2xl border border-white/10 bg-white/[0.02] px-4 py-3">
        <div className="text-[11px] uppercase tracking-[0.14em] text-neutral-500 font-semibold">
          budget
        </div>
        <div className="text-sm text-neutral-500 mt-2">loading…</div>
      </section>
    );
  }

  if (rollupQ.error || !rollupQ.data) {
    return null;
  }

  const r = rollupQ.data;
  const self = r.chain[0];
  const ancestors = r.chain.slice(1);
  const bindingAncestor =
    r.constraint_source !== "self" && r.constraint_source !== "unlimited" && r.constraint_source !== "env_default"
      ? ancestors.find((a) => a.kind === r.constraint_source)
      : undefined;

  return (
    <section className="rounded-2xl border border-white/10 bg-white/[0.02] overflow-hidden">
      <header className="flex items-center justify-between gap-3 px-4 py-3 border-b border-white/10">
        <div className="flex items-center gap-2">
          <h3 className="text-[11px] uppercase tracking-[0.14em] font-semibold text-neutral-400">
            budget
          </h3>
          <ConstraintBadge source={r.constraint_source} bindingTitle={bindingAncestor?.title} />
        </div>
        {readOnly ? null : <EditCeilingButton externalId={externalId} current={r.own_ceiling_usd} onSaved={() => qc.invalidateQueries({ queryKey: ["cost-rollup", externalId] })} />}
      </header>

      <div className="px-4 py-4">
        <SelfMeter self={self} effective_remaining={r.effective_remaining_usd} />
      </div>

      {ancestors.length > 0 ? (
        <div className="border-t border-white/5 px-4 py-3 flex flex-col gap-2">
          <div className="text-[10px] uppercase tracking-[0.14em] text-neutral-600 font-semibold">
            inherited from
          </div>
          {ancestors.map((a) => (
            <AncestorRow key={a.ticket_id} breakdown={a} isBinding={bindingAncestor?.ticket_id === a.ticket_id} />
          ))}
        </div>
      ) : null}
    </section>
  );
}

function SelfMeter({
  self,
  effective_remaining,
}: {
  self: BudgetBreakdown;
  effective_remaining: number | null;
}) {
  const ceiling = self.own_ceiling_usd;
  const spent = self.spent_usd;
  const remaining = self.remaining_usd ?? effective_remaining;

  // Pct vs whichever ceiling is binding (own first, else effective via ancestor).
  let pct: number | null = null;
  let total: number | null = null;
  if (ceiling != null) {
    total = ceiling;
    pct = Math.min(100, (spent / ceiling) * 100);
  } else if (effective_remaining != null && remaining != null) {
    // Show pct relative to (spent + remaining), since we don't know the ancestor's full ceiling here.
    total = spent + effective_remaining;
    pct = total > 0 ? Math.min(100, (spent / total) * 100) : 0;
  }

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-semibold tracking-tight tabular-nums">
            ${spent.toFixed(4)}
          </span>
          {total != null ? (
            <span className="text-sm text-neutral-500 tabular-nums">
              / ${total.toFixed(2)}
            </span>
          ) : (
            <span className="text-sm text-neutral-600 italic">unlimited</span>
          )}
        </div>
        {remaining != null ? (
          <span
            className={`text-xs tabular-nums ${
              remaining <= 0
                ? "text-rose-300"
                : remaining < 1
                  ? "text-amber-300"
                  : "text-neutral-400"
            }`}
          >
            ${remaining.toFixed(4)} left
          </span>
        ) : null}
      </div>
      {pct != null ? (
        <div className="h-1.5 rounded-full bg-white/10 overflow-hidden mt-3">
          <div
            className={`h-full transition-all ${
              pct > 90 ? "bg-rose-400" : pct > 60 ? "bg-amber-400" : "bg-blue-400"
            }`}
            style={{ width: `${pct}%` }}
          />
        </div>
      ) : null}
    </div>
  );
}

function AncestorRow({ breakdown, isBinding }: { breakdown: BudgetBreakdown; isBinding: boolean }) {
  const ceiling = breakdown.own_ceiling_usd;
  const pct = ceiling != null && ceiling > 0 ? Math.min(100, (breakdown.spent_usd / ceiling) * 100) : null;
  return (
    <div className={`rounded-xl px-3 py-2 ${isBinding ? "bg-amber-400/[0.06] ring-1 ring-amber-400/20" : "bg-white/[0.02]"}`}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] uppercase tracking-[0.14em] border ${
              breakdown.kind === "epic"
                ? "bg-purple-400/15 text-purple-200 border-purple-400/30"
                : "bg-blue-400/15 text-blue-200 border-blue-400/30"
            }`}
          >
            {breakdown.kind}
          </span>
          <span className="text-sm text-neutral-300 truncate">{breakdown.title}</span>
        </div>
        <span className="text-[11px] tabular-nums text-neutral-400 font-mono shrink-0">
          ${breakdown.spent_usd.toFixed(4)}
          {ceiling != null ? ` / $${ceiling.toFixed(2)}` : " / ∞"}
        </span>
      </div>
      {pct != null ? (
        <div className="h-1 rounded-full bg-white/10 overflow-hidden mt-2">
          <div
            className={`h-full ${pct > 90 ? "bg-rose-400" : pct > 60 ? "bg-amber-400" : "bg-blue-400/70"}`}
            style={{ width: `${pct}%` }}
          />
        </div>
      ) : null}
    </div>
  );
}

function ConstraintBadge({ source, bindingTitle }: { source: string; bindingTitle?: string }) {
  if (source === "unlimited") {
    return <span className="text-[11px] text-neutral-600">no ceiling</span>;
  }
  if (source === "self") {
    return <span className="text-[11px] text-neutral-500">own ceiling</span>;
  }
  if (source === "env_default") {
    return <span className="text-[11px] text-neutral-500">env default</span>;
  }
  return (
    <span className="text-[11px] text-amber-300">
      constrained by {source}
      {bindingTitle ? ` · ${bindingTitle}` : ""}
    </span>
  );
}

function EditCeilingButton({
  externalId,
  current,
  onSaved,
}: {
  externalId: string;
  current: number | null;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState<string>(current != null ? String(current) : "");

  const mut = useMutation({
    mutationFn: (next: number | null) => api.updateBudget(externalId, next),
    onSuccess: () => {
      setEditing(false);
      onSaved();
    },
  });

  if (!editing) {
    return (
      <button
        onClick={() => {
          setValue(current != null ? String(current) : "");
          setEditing(true);
        }}
        className="text-[11px] text-neutral-500 hover:text-neutral-200 transition"
      >
        {current != null ? "edit ceiling" : "set ceiling"}
      </button>
    );
  }

  const trimmed = value.trim();
  const parsed = trimmed === "" ? null : Number(trimmed);
  const invalid = trimmed !== "" && (Number.isNaN(parsed) || (parsed as number) < 0);

  return (
    <form
      className="flex items-center gap-1.5"
      onSubmit={(e) => {
        e.preventDefault();
        if (invalid) return;
        mut.mutate(parsed);
      }}
    >
      <span className="text-[11px] text-neutral-500">$</span>
      <input
        autoFocus
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="(unset)"
        className="w-20 px-2 py-1 rounded-md bg-white/[0.04] border border-white/10 text-xs text-neutral-100 placeholder-neutral-600 focus:outline-none focus:border-blue-400/40 tabular-nums"
      />
      <button
        type="submit"
        disabled={invalid || mut.isPending}
        className="px-2 py-1 rounded-md bg-blue-500 hover:bg-blue-400 text-white text-[11px] disabled:opacity-40"
      >
        {mut.isPending ? "…" : "save"}
      </button>
      <button
        type="button"
        onClick={() => setEditing(false)}
        className="text-[11px] text-neutral-500 hover:text-neutral-300"
      >
        cancel
      </button>
      {mut.error ? (
        <span className="text-[11px] text-rose-400 ml-1">{(mut.error as Error).message}</span>
      ) : null}
    </form>
  );
}

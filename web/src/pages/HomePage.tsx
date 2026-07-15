import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, Persona, Ticket, TicketKind } from "../lib/api";
import { SortKey, useHomeViewState } from "../lib/useHomeViewState";
import { ChildStatusChips } from "../components/ChildStatusChips";
import { GitHubConnectButton } from "../components/GitHubConnectButton";
import { PersonaChip } from "../components/PersonaChip";
import { PersonaSpendCard } from "../components/PersonaSpendCard";
import { StackSpendCard } from "../components/StackSpendCard";
import { StatusBadge } from "../components/StatusBadge";

// Tickets in these statuses need human action on the plan.
const NEEDS_REVIEW = new Set(["planning"]);
// Closed states.
const CLOSED = new Set(["pr_open", "merged", "failed", "cancelled"]);

// Status sort order — earlier in the lifecycle first so "by status" surfaces
// what's blocking next.
const STATUS_ORDER: Record<string, number> = {
  pending: 0,
  planning: 1,
  plan_approved: 2,
  in_progress: 3,
  pr_open: 4,
  merged: 5,
  failed: 6,
  cancelled: 7,
};

const SORT_LABELS: Record<SortKey, string> = {
  updated_desc: "Most recent",
  updated_asc: "Oldest",
  title: "Title A–Z",
  status: "By status",
};

function matchesSearch(t: Ticket, query: string): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  return (
    t.title.toLowerCase().includes(q) ||
    t.external_id.toLowerCase().includes(q)
  );
}

function sortTickets(tickets: Ticket[], by: SortKey): Ticket[] {
  const arr = [...tickets];
  switch (by) {
    case "updated_desc":
      return arr.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    case "updated_asc":
      return arr.sort((a, b) => a.updated_at.localeCompare(b.updated_at));
    case "title":
      return arr.sort((a, b) => a.title.localeCompare(b.title));
    case "status":
      return arr.sort(
        (a, b) =>
          (STATUS_ORDER[a.status] ?? 99) - (STATUS_ORDER[b.status] ?? 99) ||
          b.updated_at.localeCompare(a.updated_at),
      );
  }
}

export function HomePage() {
  const nav = useNavigate();
  const qc = useQueryClient();

  // View state (kind / sort / search) lives in a reducer so the choice
  // survives navigation — `inFlightKind` and `sortBy` are persisted to
  // localStorage; `search` stays ephemeral to avoid stale-filter surprises.
  const { state: view, setKind, setSort, setSearch } = useHomeViewState();
  const { inFlightKind, sortBy, search } = view;

  // Bulk-select state. Selecting any row reveals a sticky action bar at
  // the bottom of the viewport for deleting in batch.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const toggleSelected = (externalId: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(externalId)) next.delete(externalId);
      else next.add(externalId);
      return next;
    });
  const clearSelection = () => setSelected(new Set());
  /** Tri-state header toggle: if every id is already selected, remove them
   * all; otherwise add the missing ones. Lets "click again to deselect"
   * fall out naturally. */
  const toggleMany = (externalIds: string[]) =>
    setSelected((prev) => {
      const all = externalIds.every((id) => prev.has(id));
      const next = new Set(prev);
      if (all) for (const id of externalIds) next.delete(id);
      else for (const id of externalIds) next.add(id);
      return next;
    });

  const ticketsQ = useQuery({
    queryKey: ["tickets"],
    queryFn: () => api.listTickets(),
    refetchInterval: 5_000, // home page is a dashboard — poll for new arrivals
  });

  // Catalog lookup for the PersonaChip — fetched once, very cacheable.
  const personasQ = useQuery({
    queryKey: ["personas"],
    queryFn: () => api.listPersonas(),
    staleTime: 5 * 60_000,
  });
  const personaCatalog = personasQ.data ?? null;

  // Stack catalog — friendly names for the StackSpendCard. Same caching.
  const stacksQ = useQuery({
    queryKey: ["stacks"],
    queryFn: () => api.listStacks(),
    staleTime: 5 * 60_000,
  });
  const stackCatalog = stacksQ.data ?? null;

  const bulkDeleteMut = useMutation({
    mutationFn: (ids: string[]) => api.bulkDeleteTickets(ids),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["tickets"] });
      qc.invalidateQueries({ queryKey: ["children"] });
      qc.invalidateQueries({ queryKey: ["roadmap"] });
      clearSelection();
      console.info(
        `bulk-deleted ${res.deleted_ticket_count} ticket(s) ` +
          `(${res.deleted_root_external_ids.length} roots, ` +
          `${res.workflows_terminated} workflow(s) terminated)`,
      );
    },
  });

  function confirmBulkDelete() {
    if (selected.size === 0) return;
    const ids = [...selected];
    const ok = window.confirm(
      `Delete ${ids.length} selected ticket${ids.length === 1 ? "" : "s"} ` +
        `(and their descendants)? Active workflows will be terminated. ` +
        `This cannot be undone.`,
    );
    if (ok) bulkDeleteMut.mutate(ids);
  }

  const tickets = ticketsQ.data ?? [];
  // "Needs review" is task-specific (only tasks transition to `planning`).
  const needsReview = tickets.filter(
    (t) =>
      NEEDS_REVIEW.has(t.status) &&
      t.kind === "task" &&
      matchesSearch(t, search),
  );
  // "Closed" matches the selected kind so the view stays coherent.
  const closed = sortTickets(
    tickets.filter(
      (t) =>
        CLOSED.has(t.status) &&
        t.kind === inFlightKind &&
        matchesSearch(t, search),
    ),
    sortBy,
  );
  // "In flight" = of selected kind, anything not closed and not awaiting
  // review (those have their own section above).
  const inFlight = useMemo(
    () =>
      sortTickets(
        tickets.filter(
          (t) =>
            t.kind === inFlightKind &&
            !CLOSED.has(t.status) &&
            !NEEDS_REVIEW.has(t.status) &&
            matchesSearch(t, search),
        ),
        sortBy,
      ),
    [tickets, inFlightKind, sortBy, search],
  );

  // Enter in the search box jumps to a ticket if exactly one match remains
  // across all sections — preserves the muscle memory of the old jump-to form.
  const searchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const matches = [...needsReview, ...inFlight, ...closed];
    if (matches.length === 1) {
      nav(`/tickets/${encodeURIComponent(matches[0].external_id)}`);
    }
  };

  return (
    <div className="max-w-4xl mx-auto px-6 sm:px-8 py-12">
      <header className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="size-8 rounded-xl bg-gradient-to-br from-blue-400 to-indigo-600 shadow-lg shadow-blue-500/30 ring-1 ring-white/10" />
          <h1 className="text-3xl font-semibold tracking-tight">pravi agent</h1>
        </div>
        <div className="flex items-center gap-3">
          {ticketsQ.isFetching ? (
            <span className="text-[11px] text-neutral-500 flex items-center gap-1.5">
              <span className="size-1.5 rounded-full bg-blue-400 animate-pulse" />
              refreshing
            </span>
          ) : null}
          <GitHubConnectButton />
          <Link
            to="/issues"
            className="px-3 py-1.5 rounded-full bg-white/5 hover:bg-white/10 border border-white/10 text-sm font-medium transition"
          >
            issues
          </Link>
          <Link
            to="/runs"
            className="px-3 py-1.5 rounded-full bg-white/5 hover:bg-white/10 border border-white/10 text-sm font-medium transition"
          >
            runs
          </Link>
          <Link
            to="/new?kind=epic"
            className="px-3 py-1.5 rounded-full bg-white/5 hover:bg-white/10 border border-white/10 text-sm font-medium transition"
          >
            + epic
          </Link>
          <Link
            to="/new?kind=task"
            className="px-3.5 py-1.5 rounded-full bg-blue-500 hover:bg-blue-400 text-white text-sm font-medium shadow-lg shadow-blue-500/20 transition"
          >
            + task
          </Link>
        </div>
      </header>

      <Toolbar
        search={search}
        onSearch={setSearch}
        onSubmit={searchSubmit}
        kind={inFlightKind}
        onKindChange={setKind}
        sort={sortBy}
        onSortChange={setSort}
      />

      <Section
        title="Needs your review"
        empty={
          search
            ? "No matching tickets are waiting for a plan."
            : "No tickets are waiting for a plan."
        }
        tickets={needsReview}
        emphasised
        selected={selected}
        onToggleSelected={toggleSelected}
        onToggleAll={toggleMany}
        personaCatalog={personaCatalog}
      />

      <section className="mt-8">
        <div className="flex items-center gap-2 mb-3">
          <SelectAllCheckbox
            tickets={inFlight}
            selected={selected}
            onToggleAll={toggleMany}
          />
          <h2 className="text-[11px] uppercase tracking-[0.14em] font-semibold text-neutral-500 flex items-center gap-2">
            In flight
            <span className="text-neutral-600 normal-case tracking-normal font-normal">
              ({inFlight.length})
            </span>
          </h2>
        </div>
        {inFlight.length === 0 ? (
          <p className="text-sm text-neutral-600 italic">
            {search
              ? `No matching ${inFlightKind === "task" ? "tasks" : `${inFlightKind}s`}.`
              : `No ${inFlightKind === "task" ? "tasks" : `${inFlightKind}s`} in progress.`}
          </p>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {inFlight.map((t) => (
              <TicketRow
                key={t.id}
                ticket={t}
                selected={selected.has(t.external_id)}
                onToggleSelected={toggleSelected}
                personaCatalog={personaCatalog}
              />
            ))}
          </ul>
        )}
      </section>

      <PersonaSpendCard personaCatalog={personaCatalog} />
      <StackSpendCard stackCatalog={stackCatalog} />

      <Section
        title={`Closed ${inFlightKind === "task" ? "tasks" : `${inFlightKind}s`}`}
        empty={
          search
            ? `No matching closed ${inFlightKind === "task" ? "tasks" : `${inFlightKind}s`}.`
            : `No closed ${inFlightKind === "task" ? "tasks" : `${inFlightKind}s`} yet.`
        }
        tickets={closed}
        collapsible
        forceOpen={Boolean(search)}
        selected={selected}
        onToggleSelected={toggleSelected}
        onToggleAll={toggleMany}
        personaCatalog={personaCatalog}
      />

      {selected.size > 0 ? (
        <BulkActionBar
          count={selected.size}
          onDelete={confirmBulkDelete}
          onClear={clearSelection}
          isDeleting={bulkDeleteMut.isPending}
        />
      ) : null}

      <p className="text-[11px] text-neutral-500 mt-12 leading-relaxed">
        Tickets are created from the CLI:
        <code className="ml-1.5 px-2 py-0.5 rounded-md bg-white/5 text-neutral-300 border border-white/10 font-mono">
          pravi ticket start &lt;ID&gt; --title "…" --body "…" --repo /path/to/repo --domain &lt;d&gt; --detach
        </code>
      </p>
    </div>
  );
}

/** Unified filter widget: search + kind toggle + sort selector. Visually
 * grouped into a single rounded shell so the controls read as one
 * professional toolbar rather than scattered chips. */
function Toolbar({
  search,
  onSearch,
  onSubmit,
  kind,
  onKindChange,
  sort,
  onSortChange,
}: {
  search: string;
  onSearch: (v: string) => void;
  onSubmit: (e: React.FormEvent) => void;
  kind: TicketKind;
  onKindChange: (k: TicketKind) => void;
  sort: SortKey;
  onSortChange: (s: SortKey) => void;
}) {
  return (
    <form
      onSubmit={onSubmit}
      className="mt-8 flex items-center gap-2 p-1.5 rounded-2xl bg-white/[0.02] border border-white/10 flex-wrap"
    >
      <div className="flex items-center gap-2 flex-1 min-w-[200px] px-2.5">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="size-4 text-neutral-500 shrink-0"
          aria-hidden="true"
        >
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-3.5-3.5" />
        </svg>
        <input
          type="text"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder="search tickets by title or id…"
          className="flex-1 bg-transparent py-1.5 text-sm text-neutral-100 placeholder-neutral-600 focus:outline-none"
          aria-label="search tickets"
        />
        {search ? (
          <button
            type="button"
            onClick={() => onSearch("")}
            className="text-neutral-500 hover:text-neutral-200 text-sm leading-none px-1"
            aria-label="clear search"
          >
            ×
          </button>
        ) : null}
      </div>
      <div className="flex items-center gap-2">
        <KindToggle value={kind} onChange={onKindChange} />
        <SortDropdown value={sort} onChange={onSortChange} />
      </div>
    </form>
  );
}

function KindToggle({
  value,
  onChange,
}: {
  value: TicketKind;
  onChange: (k: TicketKind) => void;
}) {
  const kinds: TicketKind[] = ["epic", "feature", "task"];
  return (
    <div className="inline-flex rounded-full border border-white/10 bg-white/[0.02] p-0.5">
      {kinds.map((k) => {
        const active = k === value;
        return (
          <button
            key={k}
            type="button"
            onClick={() => onChange(k)}
            className={`px-3 py-1 text-xs rounded-full transition ${
              active
                ? "bg-white/10 text-neutral-100"
                : "text-neutral-500 hover:text-neutral-200"
            }`}
          >
            {k === "task" ? "tasks" : `${k}s`}
          </button>
        );
      })}
    </div>
  );
}

function SortDropdown({
  value,
  onChange,
}: {
  value: SortKey;
  onChange: (k: SortKey) => void;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value as SortKey)}
      className="text-xs px-2.5 py-1.5 rounded-full bg-white/[0.02] border border-white/10 text-neutral-300 focus:outline-none focus:border-blue-400/40 transition"
      aria-label="sort"
    >
      {(Object.keys(SORT_LABELS) as SortKey[]).map((k) => (
        <option key={k} value={k}>
          {SORT_LABELS[k]}
        </option>
      ))}
    </select>
  );
}

/** Tri-state "select all" checkbox for a section.
 * - none → unchecked
 * - some → indeterminate (the dash thing)
 * - all  → checked
 * Click cycles: none/some → all → none. */
function SelectAllCheckbox({
  tickets,
  selected,
  onToggleAll,
}: {
  tickets: Ticket[];
  selected?: Set<string>;
  onToggleAll: (externalIds: string[]) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const ids = useMemo(() => tickets.map((t) => t.external_id), [tickets]);
  const count = ids.filter((id) => selected?.has(id)).length;
  const state: "none" | "some" | "all" =
    count === 0 ? "none" : count === ids.length ? "all" : "some";
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = state === "some";
  }, [state]);

  return (
    <input
      ref={ref}
      type="checkbox"
      checked={state === "all"}
      onChange={() => onToggleAll(ids)}
      aria-label="select all in section"
      title={
        state === "all"
          ? "deselect all"
          : state === "some"
            ? "select all"
            : "select all"
      }
      className="size-4 shrink-0 rounded border-white/20 bg-white/5 accent-rose-400"
    />
  );
}

function BulkActionBar({
  count,
  onDelete,
  onClear,
  isDeleting,
}: {
  count: number;
  onDelete: () => void;
  onClear: () => void;
  isDeleting: boolean;
}) {
  return (
    <div className="fixed bottom-5 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 px-4 py-2.5 rounded-full bg-neutral-900/95 border border-white/15 shadow-2xl shadow-black/40 backdrop-blur">
      <span className="text-sm text-neutral-200 tabular-nums">
        {count} selected
      </span>
      <span className="text-neutral-600">·</span>
      <button
        onClick={onDelete}
        disabled={isDeleting}
        className="px-3 py-1 rounded-full bg-rose-500 hover:bg-rose-400 text-white text-xs font-medium shadow-lg shadow-rose-500/25 disabled:opacity-40 transition"
      >
        {isDeleting ? "deleting…" : `delete ${count}`}
      </button>
      <button
        onClick={onClear}
        disabled={isDeleting}
        className="px-3 py-1 rounded-full bg-white/5 hover:bg-white/10 border border-white/10 text-neutral-300 text-xs disabled:opacity-40 transition"
      >
        clear
      </button>
    </div>
  );
}

function Section({
  title,
  empty,
  tickets,
  emphasised,
  collapsible,
  forceOpen,
  selected,
  onToggleSelected,
  onToggleAll,
  personaCatalog,
}: {
  title: string;
  empty: string;
  tickets: Ticket[];
  emphasised?: boolean;
  collapsible?: boolean;
  /** When true, force the collapsible section open (e.g. while a search
   * is active, so matches inside aren't hidden). */
  forceOpen?: boolean;
  selected?: Set<string>;
  onToggleSelected?: (externalId: string) => void;
  onToggleAll?: (externalIds: string[]) => void;
  /** Persona catalog for the per-row chip; null while still loading. */
  personaCatalog?: Persona[] | null;
}) {
  const header = (children: React.ReactNode) => (
    <div className="flex items-center gap-2 mb-3">
      {onToggleAll && tickets.length > 0 ? (
        <SelectAllCheckbox tickets={tickets} selected={selected} onToggleAll={onToggleAll} />
      ) : null}
      {children}
    </div>
  );
  const Inner = (
    <>
      {header(
        <h2 className="text-[11px] uppercase tracking-[0.14em] font-semibold text-neutral-500">
          {title}
        </h2>,
      )}
      {tickets.length === 0 ? (
        <p className="text-sm text-neutral-600 italic">{empty}</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {tickets.map((t) => (
            <TicketRow
              key={t.id}
              ticket={t}
              emphasised={emphasised}
              selected={selected?.has(t.external_id) ?? false}
              onToggleSelected={onToggleSelected}
              personaCatalog={personaCatalog}
            />
          ))}
        </ul>
      )}
    </>
  );

  if (collapsible) {
    return (
      <details className="mt-8 group" open={forceOpen || undefined}>
        <summary className="cursor-pointer text-[11px] uppercase tracking-[0.14em] font-semibold text-neutral-500 hover:text-neutral-300 transition flex items-center gap-2 list-none">
          <span className="inline-block transition-transform group-open:rotate-90">›</span>
          {title} <span className="text-neutral-600 normal-case tracking-normal">({tickets.length})</span>
        </summary>
        <div className="mt-3">
          {onToggleAll && tickets.length > 0 ? (
            <div className="mb-2">
              <SelectAllCheckbox
                tickets={tickets}
                selected={selected}
                onToggleAll={onToggleAll}
              />
              <span className="text-[11px] text-neutral-500 ml-2">select all</span>
            </div>
          ) : null}
          {tickets.length === 0 ? (
            <p className="text-sm text-neutral-600 italic">{empty}</p>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {tickets.map((t) => (
                <TicketRow
                  key={t.id}
                  ticket={t}
                  selected={selected?.has(t.external_id) ?? false}
                  onToggleSelected={onToggleSelected}
                />
              ))}
            </ul>
          )}
        </div>
      </details>
    );
  }

  return <section className="mt-8">{Inner}</section>;
}

const KIND_PILL: Record<string, string> = {
  epic: "bg-purple-400/15 text-purple-200 border-purple-400/30",
  feature: "bg-blue-400/15 text-blue-200 border-blue-400/30",
  task: "bg-neutral-400/10 text-neutral-300 border-neutral-400/20",
};

function TicketRow({
  ticket,
  emphasised,
  selected = false,
  onToggleSelected,
  personaCatalog,
}: {
  ticket: Ticket;
  emphasised?: boolean;
  selected?: boolean;
  onToggleSelected?: (externalId: string) => void;
  personaCatalog?: Persona[] | null;
}) {
  // When selected, override the per-emphasis hover state with a clear
  // selected look so the user can tell at a glance what they've picked.
  const base = selected
    ? "border-rose-400/40 bg-rose-400/[0.06] hover:bg-rose-400/[0.10]"
    : emphasised
      ? "border-amber-400/20 bg-amber-400/[0.04] hover:bg-amber-400/[0.08] hover:border-amber-400/30"
      : "border-white/10 bg-white/[0.03] hover:bg-white/[0.06] hover:border-white/15";
  return (
    <li className={`flex items-center gap-2 border rounded-2xl pl-3 pr-1 transition ${base}`}>
      {onToggleSelected ? (
        <input
          type="checkbox"
          checked={selected}
          onChange={() => onToggleSelected(ticket.external_id)}
          aria-label={`select ${ticket.external_id}`}
          // Stop the click from bubbling to the parent `<Link>` so toggling
          // doesn't also navigate.
          onClick={(e) => e.stopPropagation()}
          className="size-4 shrink-0 rounded border-white/20 bg-white/5 accent-rose-400"
        />
      ) : null}
      <Link
        to={`/tickets/${encodeURIComponent(ticket.external_id)}`}
        className="flex-1 min-w-0 flex items-center gap-3 px-2 py-3"
      >
        <span
          className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] uppercase tracking-[0.14em] border shrink-0 ${KIND_PILL[ticket.kind] || KIND_PILL.task}`}
        >
          {ticket.kind}
        </span>
        <div className="flex-1 min-w-0">
          <div className="font-medium truncate text-neutral-100 flex items-center gap-2">
            <span className="truncate">{ticket.title}</span>
            {ticket.persona || ticket.stack ? (
              <PersonaChip
                persona={ticket.persona}
                stack={ticket.stack}
                catalog={personaCatalog ?? null}
              />
            ) : null}
          </div>
          <div className="text-[11px] text-neutral-500 font-mono truncate mt-0.5">
            {ticket.external_id} · {ticket.repo.name} · {ticket.domain_name || "—"}
            {ticket.child_count > 0
              ? ` · ${ticket.child_count} child${ticket.child_count === 1 ? "" : "ren"}`
              : ""}
          </div>
          {ticket.kind !== "task" ? (
            <div className="mt-1">
              <ChildStatusChips ticket={ticket} />
            </div>
          ) : null}
        </div>
        <StatusBadge status={ticket.status} />
      </Link>
    </li>
  );
}

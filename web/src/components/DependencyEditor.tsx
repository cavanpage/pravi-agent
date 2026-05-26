import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, Ticket } from "../lib/api";

/**
 * Feature-only: edit which sibling features this feature depends on.
 *
 * Lists current prerequisites (with × to remove) and a dropdown of eligible
 * siblings to add. Server rejects cycles and same-epic violations, so we
 * just surface errors inline.
 */
export function DependencyEditor({ feature }: { feature: Ticket }) {
  const qc = useQueryClient();
  const [adding, setAdding] = useState("");
  const [error, setError] = useState<string | null>(null);

  // We need: (1) current prereqs of this feature, (2) all sibling features
  // of the same epic. Both come from the parent epic's roadmap.
  const roadmapQ = useQuery({
    queryKey: ["roadmap", feature.parent_external_id],
    queryFn: () =>
      feature.parent_external_id
        ? api.getRoadmap(feature.parent_external_id)
        : Promise.resolve(null),
    enabled: !!feature.parent_external_id,
  });

  const self = roadmapQ.data?.waves
    .flatMap((w) => w.features)
    .find((f) => f.external_id === feature.external_id);
  const siblings =
    roadmapQ.data?.waves.flatMap((w) => w.features).filter(
      (f) => f.external_id !== feature.external_id,
    ) ?? [];
  const currentDeps = self?.prerequisite_external_ids ?? [];
  const addable = siblings.filter((s) => !currentDeps.includes(s.external_id));

  const addMut = useMutation({
    mutationFn: (prereq: string) => api.addDependency(feature.external_id, prereq),
    onSuccess: () => {
      setAdding("");
      setError(null);
      qc.invalidateQueries({ queryKey: ["roadmap", feature.parent_external_id] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const removeMut = useMutation({
    mutationFn: (prereq: string) => api.deleteDependency(feature.external_id, prereq),
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["roadmap", feature.parent_external_id] });
    },
    onError: (e: Error) => setError(e.message),
  });

  if (!feature.parent_external_id) {
    return null; // dependencies only make sense inside an epic
  }

  return (
    <section className="rounded-2xl border border-white/10 bg-white/[0.02] p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="text-[11px] uppercase tracking-[0.14em] font-semibold text-neutral-500">
          dependencies
        </h2>
        <span className="text-[11px] text-neutral-600">
          features that must merge before this one
        </span>
      </div>

      {error ? (
        <div className="rounded-xl border border-rose-400/20 bg-rose-400/[0.06] text-rose-300 px-3 py-2 text-sm">
          {error}
        </div>
      ) : null}

      {currentDeps.length === 0 ? (
        <p className="text-sm text-neutral-500 italic">
          No prerequisites — this feature can be worked on independently.
        </p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {currentDeps.map((extId) => {
            const sib = siblings.find((s) => s.external_id === extId);
            return (
              <li
                key={extId}
                className="flex items-center justify-between gap-3 rounded-xl border border-white/10 bg-white/[0.02] px-3 py-2"
              >
                <Link
                  to={`/tickets/${encodeURIComponent(extId)}`}
                  className="text-sm text-neutral-100 hover:text-blue-300 transition truncate min-w-0"
                >
                  {sib?.title ?? extId}
                  <span className="text-[11px] text-neutral-500 font-mono ml-2">{extId}</span>
                </Link>
                <button
                  onClick={() => removeMut.mutate(extId)}
                  disabled={removeMut.isPending}
                  className="text-neutral-500 hover:text-rose-300 transition text-sm shrink-0 disabled:opacity-40"
                  title="remove dependency"
                >
                  ×
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {addable.length > 0 ? (
        <form
          className="flex gap-2 items-center"
          onSubmit={(e) => {
            e.preventDefault();
            if (adding) addMut.mutate(adding);
          }}
        >
          <select
            value={adding}
            onChange={(e) => setAdding(e.target.value)}
            className="flex-1 px-3 py-2 rounded-xl bg-white/[0.03] border border-white/10 text-sm text-neutral-100 focus:outline-none focus:border-blue-400/40 transition"
          >
            <option value="">add a prerequisite…</option>
            {addable.map((s) => (
              <option key={s.external_id} value={s.external_id}>
                {s.title} ({s.external_id})
              </option>
            ))}
          </select>
          <button
            type="submit"
            disabled={!adding || addMut.isPending}
            className="px-3 py-2 rounded-xl bg-blue-500 hover:bg-blue-400 text-white text-sm font-medium disabled:opacity-40 transition"
          >
            {addMut.isPending ? "adding…" : "add"}
          </button>
        </form>
      ) : siblings.length > 0 ? (
        <p className="text-xs text-neutral-600 italic">
          All sibling features are already prerequisites.
        </p>
      ) : null}
    </section>
  );
}

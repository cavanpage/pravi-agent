import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { api, RoadmapFeature } from "../lib/api";
import { StatusBadge } from "./StatusBadge";

/**
 * Topological roadmap for an epic: features grouped into waves.
 *
 * Wave 0 has no prerequisites. Wave N depends only on waves < N. Features
 * inside a single wave can be worked on in parallel — that's the "ideally
 * in a roadmap" view the user asked for.
 */
export function RoadmapView({
  epicExternalId,
  emptyHint,
}: {
  epicExternalId: string;
  emptyHint?: string;
}) {
  const q = useQuery({
    queryKey: ["roadmap", epicExternalId],
    queryFn: () => api.getRoadmap(epicExternalId),
    refetchInterval: 10_000,
  });

  if (q.isLoading) return <div className="text-sm text-neutral-500 italic">loading roadmap…</div>;
  if (q.error) {
    return (
      <div className="rounded-xl border border-rose-400/20 bg-rose-400/[0.06] text-rose-300 px-4 py-3 text-sm">
        roadmap error: {(q.error as Error).message}
      </div>
    );
  }
  const roadmap = q.data!;

  if (roadmap.waves.length === 0 && roadmap.cyclic_external_ids.length === 0) {
    return (
      <p className="text-sm text-neutral-600 italic">
        {emptyHint ?? "No features yet — decompose the epic or add one manually."}
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {roadmap.cyclic_external_ids.length > 0 ? (
        <div className="rounded-xl border border-amber-400/30 bg-amber-400/[0.08] text-amber-200 px-3 py-2 text-sm">
          <strong>dependency cycle:</strong> {roadmap.cyclic_external_ids.join(", ")} —
          remove an edge to resolve.
        </div>
      ) : null}

      <div className="flex gap-3 overflow-x-auto pb-2">
        {roadmap.waves.map((wave) => (
          <WaveColumn key={wave.index} index={wave.index} features={wave.features} />
        ))}
      </div>
    </div>
  );
}

function WaveColumn({
  index,
  features,
}: {
  index: number;
  features: RoadmapFeature[];
}) {
  return (
    <div className="shrink-0 w-72 flex flex-col gap-2">
      <div className="flex items-baseline justify-between text-[11px] uppercase tracking-[0.14em] text-neutral-500 font-semibold">
        <span>Wave {index + 1}</span>
        <span className="text-neutral-600 normal-case tracking-normal font-normal">
          {features.length} {features.length === 1 ? "feature" : "features"} · parallel
        </span>
      </div>
      <ul className="flex flex-col gap-2">
        {features.map((f) => (
          <RoadmapFeatureCard key={f.id} feature={f} />
        ))}
      </ul>
    </div>
  );
}

function RoadmapFeatureCard({ feature }: { feature: RoadmapFeature }) {
  return (
    <li>
      <Link
        to={`/tickets/${encodeURIComponent(feature.external_id)}`}
        className="block rounded-2xl border border-blue-400/15 bg-blue-400/[0.04] hover:bg-blue-400/[0.08] hover:border-blue-400/30 px-3.5 py-3 transition"
      >
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="text-sm font-medium text-neutral-100 leading-snug">
              {feature.title}
            </div>
            <div className="text-[11px] text-neutral-500 font-mono truncate mt-1">
              {feature.external_id} · {feature.domain_name || "—"}
              {feature.child_count > 0
                ? ` · ${feature.child_count} task${feature.child_count === 1 ? "" : "s"}`
                : ""}
            </div>
          </div>
          <StatusBadge status={feature.status} />
        </div>

        {feature.prerequisite_external_ids.length > 0 ? (
          <div className="mt-2 text-[10px] text-neutral-500">
            depends on:{" "}
            <span className="text-neutral-400 font-mono">
              {feature.prerequisite_external_ids.join(", ")}
            </span>
          </div>
        ) : null}
      </Link>
    </li>
  );
}

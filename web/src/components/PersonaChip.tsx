import { Persona } from "../lib/api";

/** Small badge showing a ticket's persona (and optionally stack). Used on
 * ticket rows + the ticket header. See ADR 0004. */

// Persona-group → color mapping. Keeps the dashboard readable without
// hard-coding per-persona colors.
const GROUP_TONE: Record<string, string> = {
  product: "bg-fuchsia-400/15 text-fuchsia-200 border-fuchsia-400/30",
  architecture: "bg-purple-400/15 text-purple-200 border-purple-400/30",
  engineering: "bg-blue-400/15 text-blue-200 border-blue-400/30",
  quality: "bg-emerald-400/15 text-emerald-200 border-emerald-400/30",
  platform: "bg-amber-400/15 text-amber-200 border-amber-400/30",
  other: "bg-neutral-400/10 text-neutral-300 border-neutral-400/20",
};

export function PersonaChip({
  persona,
  stack,
  catalog,
}: {
  persona: string | null;
  stack: string | null;
  /** The persona catalog (from `api.listPersonas()`) — used to look up
   * group/display name. Pass `null` to render with just the slug. */
  catalog: Persona[] | null;
}) {
  // No persona set → render the stack alone (if any), or nothing.
  if (!persona) {
    return stack ? <StackChip stack={stack} /> : null;
  }
  const entry = catalog?.find((p) => p.slug === persona) ?? null;
  const tone = GROUP_TONE[entry?.group ?? "other"];
  return (
    <span className="inline-flex items-center gap-1">
      <span
        className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] uppercase tracking-[0.14em] border ${tone}`}
        title={entry?.description ?? persona}
      >
        {entry?.name ?? persona}
      </span>
      {stack ? <StackChip stack={stack} /> : null}
    </span>
  );
}

function StackChip({ stack }: { stack: string }) {
  return (
    <span
      className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-mono text-neutral-400 border border-white/10 bg-white/5"
      title={`stack: ${stack}`}
    >
      {stack}
    </span>
  );
}

import { Persona } from "../lib/api";

/** Persona dropdown for the new-ticket form. Coming-soon entries are
 * shown but disabled with a "coming soon" suffix. See ADR 0004. */

export function PersonaPicker({
  value,
  onChange,
  personas,
  disabled = false,
}: {
  value: string | null;
  onChange: (v: string | null) => void;
  personas: Persona[] | null;
  disabled?: boolean;
}) {
  if (!personas) {
    return (
      <div className="rounded-xl border border-white/10 bg-white/[0.02] px-3.5 py-2.5 text-sm text-neutral-500 italic">
        loading…
      </div>
    );
  }

  const grouped = groupByGroup(personas);
  return (
    <select
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value || null)}
      disabled={disabled}
      className="w-full px-3.5 py-2.5 rounded-xl bg-white/[0.03] border border-white/10 text-sm text-neutral-100 focus:outline-none focus:border-blue-400/40 focus:bg-white/[0.05] disabled:opacity-60 transition"
    >
      <option value="">— unassigned (generic) —</option>
      {Object.entries(grouped).map(([groupName, entries]) => (
        <optgroup key={groupName} label={groupLabel(groupName)}>
          {entries.map((p) => {
            const isComingSoon = p.status === "coming_soon";
            return (
              <option
                key={p.slug}
                value={p.slug}
                disabled={isComingSoon}
                title={p.description}
              >
                {p.name}
                {isComingSoon ? " — coming soon" : ""}
              </option>
            );
          })}
        </optgroup>
      ))}
    </select>
  );
}

const GROUP_ORDER = [
  "product",
  "architecture",
  "engineering",
  "quality",
  "platform",
  "other",
];

function groupByGroup(personas: Persona[]): Record<string, Persona[]> {
  const out: Record<string, Persona[]> = {};
  for (const p of personas) {
    if (!out[p.group]) out[p.group] = [];
    out[p.group].push(p);
  }
  // Re-order keys by GROUP_ORDER so dropdowns look stable.
  return Object.fromEntries(
    GROUP_ORDER.filter((g) => g in out).map((g) => [g, out[g]]),
  );
}

function groupLabel(g: string): string {
  switch (g) {
    case "product":
      return "Product & Definition";
    case "architecture":
      return "Architecture & Foundation";
    case "engineering":
      return "Engineering";
    case "quality":
      return "Quality & Validation";
    case "platform":
      return "Platform, Ops & Security";
    case "other":
      return "Other";
    default:
      return g;
  }
}

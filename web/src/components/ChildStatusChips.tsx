/** Compact breakdown of a feature/epic's descendant-task statuses.
 *
 * Renders one chip per non-zero bucket (only the buckets present),
 * sorted by lifecycle order so the visual reads left→right as
 * "earliest stage → done". `failed` is always shown at the end in
 * red. */

import type { Ticket } from "../lib/api";

const ORDER = [
  "pending",
  "planning",
  "plan_approved",
  "in_progress",
  "pr_open",
  "merged",
  "cancelled",
  "failed",
] as const;

const LABEL: Record<string, string> = {
  pending: "pending",
  planning: "planning",
  plan_approved: "approved",
  in_progress: "running",
  pr_open: "PR open",
  merged: "merged",
  cancelled: "cancelled",
  failed: "failed",
};

const TONE: Record<string, string> = {
  pending: "text-neutral-400 bg-neutral-400/10 border-neutral-400/20",
  planning: "text-amber-200 bg-amber-400/10 border-amber-400/25",
  plan_approved: "text-amber-200 bg-amber-400/10 border-amber-400/25",
  in_progress: "text-blue-200 bg-blue-400/10 border-blue-400/30",
  pr_open: "text-emerald-200 bg-emerald-400/10 border-emerald-400/30",
  merged: "text-emerald-300 bg-emerald-500/15 border-emerald-500/30",
  cancelled: "text-neutral-400 bg-neutral-500/10 border-neutral-500/20",
  failed: "text-rose-200 bg-rose-400/15 border-rose-400/30",
};

export function ChildStatusChips({
  ticket,
  size = "sm",
}: {
  ticket: Pick<Ticket, "kind" | "child_status_counts">;
  /** "sm" for ticket rows, "lg" for ticket headers. */
  size?: "sm" | "lg";
}) {
  if (ticket.kind === "task") return null;
  const counts = ticket.child_status_counts;
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  if (total === 0) return null;

  const sized =
    size === "lg"
      ? "px-2 py-0.5 text-[11px]"
      : "px-1.5 py-0 text-[10px] leading-4";

  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {ORDER.map((slug) => {
        const n = counts[slug] ?? 0;
        if (n === 0) return null;
        return (
          <span
            key={slug}
            className={`inline-flex items-center rounded-full border tabular-nums ${sized} ${TONE[slug] ?? TONE.pending}`}
            title={`${n} ${LABEL[slug] ?? slug} of ${total}`}
          >
            {n} {LABEL[slug] ?? slug}
          </span>
        );
      })}
    </span>
  );
}

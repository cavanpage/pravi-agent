type Props = { status: string; tone?: "workflow" | "execution" };

const COLORS: Record<string, string> = {
  // FeatureWorkflow @workflow.query current_status values
  loading_ticket: "bg-white/5 text-neutral-400 ring-white/10",
  waiting_for_plan: "bg-amber-400/10 text-amber-300 ring-amber-400/20",
  running_dev: "bg-blue-400/10 text-blue-300 ring-blue-400/20",
  done: "bg-emerald-400/10 text-emerald-300 ring-emerald-400/20",
  cancelled: "bg-rose-400/10 text-rose-300 ring-rose-400/20",
  // Temporal execution statuses
  RUNNING: "bg-blue-400/10 text-blue-300 ring-blue-400/20",
  COMPLETED: "bg-emerald-400/10 text-emerald-300 ring-emerald-400/20",
  FAILED: "bg-rose-400/10 text-rose-300 ring-rose-400/20",
  CANCELED: "bg-rose-400/10 text-rose-300 ring-rose-400/20",
  TERMINATED: "bg-rose-400/10 text-rose-300 ring-rose-400/20",
  TIMED_OUT: "bg-rose-400/10 text-rose-300 ring-rose-400/20",
};

export function StatusBadge({ status, tone = "workflow" }: Props) {
  const colors = COLORS[status] || "bg-white/5 text-neutral-400 ring-white/10";
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-mono tracking-wide ring-1 ring-inset ${colors}`}
      title={tone === "execution" ? "Temporal execution status" : "Workflow phase"}
    >
      <span className="size-1.5 rounded-full bg-current opacity-80" />
      {tone === "execution" ? <span className="opacity-60">exec:</span> : null}
      {status}
    </span>
  );
}

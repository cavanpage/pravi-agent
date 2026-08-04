import { ModelOption } from "../lib/api";

/**
 * A single per-stage model dropdown. "default" (value=null) means "inherit
 * from parent → env → SDK default" — matches backend semantics in
 * `pravi.agents.models_config.resolve_stage_model`.
 *
 * Pure — parent owns state. Kept tiny on purpose so it slots into the
 * ticket form (and, later, an inline editor on the ticket page).
 */
export function ModelPickerRow({
  label,
  hint,
  models,
  value,
  onChange,
  disabled,
}: {
  /** Short label displayed above the dropdown, e.g. "Clarify" or "Dev agent". */
  label: string;
  /** One-line hint about when this stage runs. */
  hint: string;
  /** Curated list from GET /api/models. */
  models: ModelOption[] | undefined;
  /** Current pinned model id, or null for "default (inherit)". */
  value: string | null;
  onChange: (next: string | null) => void;
  disabled?: boolean;
}) {
  return (
    <div className="grid grid-cols-[9rem_1fr] gap-3 items-start">
      <div className="pt-2">
        <div className="text-xs font-medium text-neutral-200">{label}</div>
        <div className="text-[11px] text-neutral-500 leading-snug mt-0.5">{hint}</div>
      </div>
      <select
        value={value ?? ""}
        disabled={disabled || !models}
        onChange={(e) => onChange(e.target.value ? e.target.value : null)}
        className="w-full px-3 py-2 rounded-xl bg-white/[0.03] border border-white/10 text-sm text-neutral-100 focus:outline-none focus:border-blue-400/40 focus:bg-white/[0.05] disabled:opacity-60 transition"
      >
        <option value="">default (inherit)</option>
        {models?.map((m) => (
          <option key={m.id} value={m.id}>
            {m.label} — {m.hint}
          </option>
        ))}
      </select>
    </div>
  );
}

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Props = {
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
};

export function PlanEditor({ value, onChange, disabled }: Props) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 h-full">
      <div className="flex flex-col">
        <label className="text-[10px] uppercase tracking-[0.14em] text-neutral-500 mb-2 font-semibold">
          plan · markdown
        </label>
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          spellCheck={false}
          className="flex-1 min-h-[420px] w-full p-4 font-mono text-sm leading-relaxed text-neutral-100 placeholder-neutral-600 rounded-2xl bg-white/[0.03] border border-white/10 focus:outline-none focus:border-blue-400/40 focus:bg-white/[0.05] disabled:opacity-60 transition resize-none"
        />
      </div>
      <div className="flex flex-col">
        <label className="text-[10px] uppercase tracking-[0.14em] text-neutral-500 mb-2 font-semibold">
          preview
        </label>
        <div className="flex-1 min-h-[420px] w-full p-4 overflow-auto rounded-2xl bg-white/[0.02] border border-white/10 markdown-body">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{value || "_empty_"}</ReactMarkdown>
        </div>
      </div>
    </div>
  );
}

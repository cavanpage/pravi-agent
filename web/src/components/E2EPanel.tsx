/**
 * Deploy + acceptance-test report for one ticket (ADR 0007).
 *
 * The verdict is a separate axis from the ticket's workflow status — a PR
 * can be open while its acceptance tests are red — so this renders next to
 * <LiveRunPanel> rather than folding into the status badge.
 *
 * Polls while the loop could still be running; goes idle once the verdict
 * is terminal.
 */
import { useQuery } from "@tanstack/react-query";

import { api, type E2EFailure, type E2EVerdict, type Preview } from "../lib/api";

const VERDICT_STYLE: Record<
  E2EVerdict,
  { label: string; tone: "emerald" | "rose" | "amber" | "neutral"; blurb: string }
> = {
  passed: {
    label: "e2e passed",
    tone: "emerald",
    blurb: "Every acceptance criterion verified against the live preview.",
  },
  failing: {
    label: "e2e failing",
    tone: "rose",
    blurb: "The deployed preview did not satisfy every acceptance criterion.",
  },
  build_failed: {
    label: "build failed",
    tone: "amber",
    blurb: "Cloudflare never produced a testable deployment, so no tests ran.",
  },
  timed_out: {
    label: "preview timed out",
    tone: "amber",
    blurb:
      "The preview build didn't finish in time. Nothing is necessarily broken — check Cloudflare and re-run.",
  },
  not_run: {
    label: "not run",
    tone: "neutral",
    blurb: "The acceptance tests haven't run for this ticket yet.",
  },
  skipped_no_criteria: {
    label: "no criteria",
    tone: "neutral",
    blurb:
      "This ticket carries no acceptance criteria, so there was nothing to verify end-to-end.",
  },
  skipped_no_config: {
    label: "preview not configured",
    tone: "neutral",
    blurb:
      "This repo has no `preview:` block in .builder/domains.yaml, or no Cloudflare Pages project is linked.",
  },
};

const TONE_CLASS = {
  emerald: "bg-emerald-400/10 text-emerald-300 border-emerald-400/20",
  rose: "bg-rose-400/10 text-rose-300 border-rose-400/20",
  amber: "bg-amber-400/10 text-amber-300 border-amber-400/20",
  neutral: "bg-white/[0.04] text-neutral-400 border-white/10",
} as const;

const DOT_CLASS = {
  emerald: "bg-emerald-400",
  rose: "bg-rose-400",
  amber: "bg-amber-400",
  neutral: "bg-neutral-600",
} as const;

/** Verdicts that can still change while the loop runs. */
const IN_FLIGHT: ReadonlySet<string> = new Set(["not_run"]);

export function E2EPanel({
  externalId,
  active,
}: {
  externalId: string;
  /** True while the ticket's workflow is still working — drives polling. */
  active: boolean;
}) {
  const { data, error } = useQuery<Preview>({
    queryKey: ["preview", externalId],
    queryFn: () => api.getPreview(externalId),
    // Poll only while an outcome could still change; a settled verdict
    // never moves on its own.
    refetchInterval: (q) => {
      const v = q.state.data?.e2e_verdict;
      return active && (v == null || IN_FLIGHT.has(v)) ? 5000 : false;
    },
  });

  if (error) return null;
  // Nothing to say until the leg has produced a verdict or a URL.
  if (!data || (!data.e2e_verdict && !data.preview_url)) return null;

  const style = VERDICT_STYLE[data.e2e_verdict ?? "not_run"];
  const skipped = (data.e2e_verdict ?? "").startsWith("skipped");

  return (
    <section className="rounded-2xl border border-white/10 bg-white/[0.02] overflow-hidden">
      <header className="flex items-center justify-between gap-4 px-4 py-3 border-b border-white/10">
        <div className="flex items-center gap-2">
          <span className={`size-1.5 rounded-full ${DOT_CLASS[style.tone]}`} />
          <h3 className="text-[11px] uppercase tracking-[0.14em] font-semibold text-neutral-400">
            end-to-end
          </h3>
          <span
            className={`text-[11px] px-2 py-0.5 rounded-full border ${TONE_CLASS[style.tone]}`}
          >
            {style.label}
          </span>
          {data.e2e_attempts > 1 ? (
            <span className="text-[11px] text-neutral-500">
              after {data.e2e_attempts} attempts
            </span>
          ) : null}
        </div>
        {data.preview_url ? (
          <a
            href={data.preview_url}
            target="_blank"
            rel="noreferrer"
            className="text-[11px] text-blue-300 hover:text-blue-200 font-mono truncate max-w-[45%]"
            title={data.preview_url}
          >
            {data.preview_url.replace(/^https?:\/\//, "")} ↗
          </a>
        ) : null}
      </header>

      <p className="px-4 py-2.5 text-xs text-neutral-500 border-b border-white/10 leading-relaxed">
        {style.blurb}
      </p>

      {/* Infra failure — the suite never got to report on the app itself. */}
      {data.error ? (
        <div className="border-b border-amber-400/20 bg-amber-400/[0.06] px-4 py-3">
          <div className="text-[11px] uppercase tracking-[0.14em] font-semibold text-amber-200/80">
            couldn't run {data.stage ? `(${data.stage})` : null}
          </div>
          <div className="text-sm mt-1 leading-relaxed text-amber-200 font-mono break-words">
            {data.error}
          </div>
        </div>
      ) : null}

      {!skipped && data.total > 0 ? (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-white/5">
          <Stat label="tests" value={String(data.total)} />
          <Stat label="passed" value={String(data.passed_count)} />
          <Stat label="failed" value={String(data.failed_count)} />
          <Stat label="attempts" value={String(data.e2e_attempts || 0)} />
        </div>
      ) : null}

      {data.failures.length > 0 ? (
        <div className="max-h-[420px] overflow-y-auto divide-y divide-white/5">
          {data.failures.map((f, i) => (
            <FailureRow key={`${f.file}:${f.line}:${i}`} failure={f} />
          ))}
        </div>
      ) : null}

      {data.production_url ? (
        <footer className="px-4 py-2.5 border-t border-white/10 text-[11px] text-neutral-500">
          production:{" "}
          <a
            href={data.production_url}
            target="_blank"
            rel="noreferrer"
            className="text-neutral-400 hover:text-neutral-200 font-mono"
          >
            {data.production_url.replace(/^https?:\/\//, "")} ↗
          </a>
        </footer>
      ) : null}
    </section>
  );
}

function FailureRow({ failure }: { failure: E2EFailure }) {
  return (
    <div className="px-4 py-3">
      <div className="flex items-baseline gap-2 flex-wrap">
        <span className="text-xs font-mono text-neutral-500">
          {failure.file}
          {failure.line ? `:${failure.line}` : ""}
        </span>
        <span className="text-sm text-neutral-200">{failure.title}</span>
      </div>
      {failure.message ? (
        <pre className="mt-2 text-[11px] leading-relaxed text-rose-200/90 font-mono whitespace-pre-wrap break-words max-h-40 overflow-y-auto">
          {failure.message}
        </pre>
      ) : null}
      {failure.snippet ? (
        <details className="mt-2">
          <summary className="text-[11px] text-neutral-500 cursor-pointer hover:text-neutral-300">
            source
          </summary>
          <pre className="mt-1.5 text-[11px] leading-relaxed text-neutral-400 font-mono whitespace-pre-wrap break-words max-h-48 overflow-y-auto">
            {failure.snippet}
          </pre>
        </details>
      ) : null}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-neutral-950 px-4 py-3">
      <div className="text-[10px] uppercase tracking-[0.14em] text-neutral-500 font-semibold">
        {label}
      </div>
      <div className="text-sm text-neutral-200 mt-0.5 font-mono">{value}</div>
    </div>
  );
}

export default E2EPanel;

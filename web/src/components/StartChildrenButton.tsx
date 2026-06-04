import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api, StartChildrenResult } from "../lib/api";

/** "Start all nested" button + dry-run confirmation modal. Lives on the
 * epic + feature ticket pages.
 *
 * Two-step interaction:
 *   1. Click the button → fires dry_run=true → modal shows which tasks
 *      will start, which are blocked, and why.
 *   2. Confirm → fires dry_run=false → workflows actually launch.
 *
 * Skips the architect plan step on each launched workflow (see ADR
 * commentary in routes.py::start_children) — review at PR time. */

export function StartChildrenButton({
  externalId,
  parentKind,
  baseRef = "main",
  pendingCount,
}: {
  externalId: string;
  parentKind: "epic" | "feature";
  baseRef?: string;
  /** Count of `pending`-status descendant tasks. When 0 the button is
   * disabled because there's nothing to start. Caller passes from the
   * ticket's `child_status_counts.pending`. */
  pendingCount?: number;
}) {
  const qc = useQueryClient();
  const [preview, setPreview] = useState<StartChildrenResult | null>(null);

  const previewMut = useMutation({
    mutationFn: () =>
      api.startChildren(externalId, { dryRun: true, baseRef }),
    onSuccess: (res) => setPreview(res),
  });

  const confirmMut = useMutation({
    mutationFn: () =>
      api.startChildren(externalId, { dryRun: false, baseRef }),
    onSuccess: (res) => {
      // Invalidate ticket lists + roadmap so the UI reflects new
      // workflows in flight.
      qc.invalidateQueries({ queryKey: ["tickets"] });
      qc.invalidateQueries({ queryKey: ["children", externalId] });
      qc.invalidateQueries({ queryKey: ["roadmap", externalId] });
      // Reuse the same modal to show the outcome — what actually
      // started + anything that surprised us in the live launch.
      setPreview(res);
    },
  });

  const nothingPending = pendingCount === 0;
  const disabled = previewMut.isPending || confirmMut.isPending || nothingPending;
  const buttonLabel = nothingPending
    ? "all nested tasks started"
    : previewMut.isPending
      ? "checking…"
      : confirmMut.isPending
        ? "starting…"
        : pendingCount != null
          ? `▶ start ${pendingCount} nested task${pendingCount === 1 ? "" : "s"}`
          : `▶ start all nested tasks`;

  return (
    <>
      <button
        type="button"
        onClick={() => previewMut.mutate()}
        disabled={disabled}
        className="px-3.5 py-1.5 rounded-full bg-emerald-500 hover:bg-emerald-400 text-white text-sm font-medium shadow-lg shadow-emerald-500/25 disabled:opacity-40 disabled:shadow-none disabled:cursor-not-allowed transition"
        title={
          nothingPending
            ? "no pending tasks left to start"
            : `Start every ready task under this ${parentKind}. Skips the architect plan step — review at PR time.`
        }
      >
        {buttonLabel}
      </button>

      {previewMut.error ? (
        <span className="ml-3 text-xs text-rose-400">
          {(previewMut.error as Error).message}
        </span>
      ) : null}

      {preview ? (
        <StartChildrenModal
          parentKind={parentKind}
          result={preview}
          onClose={() => setPreview(null)}
          onConfirm={() => confirmMut.mutate()}
          isConfirming={confirmMut.isPending}
        />
      ) : null}
    </>
  );
}

function StartChildrenModal({
  parentKind,
  result,
  onClose,
  onConfirm,
  isConfirming,
}: {
  parentKind: "epic" | "feature";
  result: StartChildrenResult;
  onClose: () => void;
  onConfirm: () => void;
  isConfirming: boolean;
}) {
  const isDryRun = result.dry_run;
  const startedLabel = isDryRun ? "will start" : "started";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl rounded-2xl border border-white/10 bg-neutral-950 p-6 flex flex-col gap-4 max-h-[80vh] overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">
              {isDryRun
                ? `Start all nested tasks under this ${parentKind}?`
                : `Started ${result.started.length} workflow${result.started.length === 1 ? "" : "s"}`}
            </h2>
            <p className="text-xs text-neutral-500 mt-1">
              Each task launches a workflow that **skips the plan step** —
              the dev agent works directly from the task body. Review
              happens at PR time.
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-neutral-500 hover:text-neutral-200 text-lg leading-none px-2"
            aria-label="close"
          >
            ×
          </button>
        </header>

        {/* Started column */}
        <section>
          <h3 className="text-[11px] uppercase tracking-[0.14em] font-semibold text-emerald-200 mb-2">
            {startedLabel} ({result.started.length})
          </h3>
          {result.started.length === 0 ? (
            <p className="text-sm text-neutral-500 italic">
              No tasks are ready to start.
            </p>
          ) : (
            <ul className="flex flex-col gap-1">
              {result.started.map((ext) => (
                <li
                  key={ext}
                  className="text-xs font-mono text-neutral-300 px-2 py-1 rounded-md bg-emerald-400/[0.06] border border-emerald-400/20"
                >
                  {ext}
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Skipped column */}
        {result.skipped.length > 0 ? (
          <section>
            <h3 className="text-[11px] uppercase tracking-[0.14em] font-semibold text-amber-200 mb-2">
              skipped ({result.skipped.length})
            </h3>
            <ul className="flex flex-col gap-1">
              {result.skipped.map((s) => (
                <li
                  key={s.external_id}
                  className="text-xs px-2 py-1 rounded-md bg-amber-400/[0.04] border border-amber-400/20"
                >
                  <span className="font-mono text-neutral-300 mr-2">
                    {s.external_id}
                  </span>
                  <span className="text-neutral-400">{s.title}</span>
                  <div className="text-amber-200/80 mt-0.5">{s.reason}</div>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        <footer className="flex items-center gap-3 flex-wrap mt-2">
          {isDryRun ? (
            <>
              <button
                type="button"
                onClick={onConfirm}
                disabled={isConfirming || result.started.length === 0}
                className="px-4 py-2 rounded-full bg-emerald-500 hover:bg-emerald-400 text-white text-sm font-medium shadow-lg shadow-emerald-500/25 disabled:opacity-40 transition"
              >
                {isConfirming
                  ? "starting…"
                  : `start ${result.started.length} task${result.started.length === 1 ? "" : "s"}`}
              </button>
              <button
                type="button"
                onClick={onClose}
                className="px-3 py-2 rounded-full bg-white/5 hover:bg-white/10 border border-white/10 text-sm transition"
              >
                cancel
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-2 rounded-full bg-white/5 hover:bg-white/10 border border-white/10 text-sm transition"
            >
              close
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}

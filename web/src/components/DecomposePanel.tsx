import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  AgentDraft,
  api,
  ClarificationQA,
  ClarificationQuestion,
  DecomposedFeature,
  PersistedClarification,
} from "../lib/api";
import {
  ProgressEvent,
  TOOL_LABEL,
  extractProgress,
  stripProgressMarkers,
} from "../lib/progressMarkers";

/** Pull the parsed feature list out of an AgentDraft(payload). Returns []
 * if the draft isn't done yet or didn't parse. */
function draftFeatures(d: AgentDraft | null | undefined): DecomposedFeature[] {
  if (!d) return [];
  const f = (d.payload as { features?: unknown } | undefined)?.features;
  return Array.isArray(f) ? (f as DecomposedFeature[]) : [];
}

/**
 * Extract whatever questions are recognizable in a possibly-incomplete YAML
 * block. Heuristic — once status="done" arrives we replace this with the
 * canonical server-parsed list. Goal: "show questions as they appear in
 * the streamed raw_md".
 */
function partialParseQuestions(rawMd: string): ClarificationQuestion[] {
  const yamlMatch = rawMd.match(/```ya?ml\s*\n([\s\S]*)/i);
  if (!yamlMatch) return [];
  const yamlText = yamlMatch[1];
  const entries = yamlText.split(/(?:^|\n)\s*-\s+(?=text:)/);
  const out: ClarificationQuestion[] = [];
  for (let i = 1; i < entries.length; i++) {
    const entry = entries[i];
    const textMatch = entry.match(/text:\s*["']([^"'\n]*?)["']/);
    if (!textMatch) continue;
    const text = textMatch[1].trim();
    if (!text) continue;
    const whyMatch = entry.match(/why:\s*["']([^"'\n]*?)["']/);
    const why = whyMatch ? whyMatch[1].trim() : "";
    // Inline options block (multi-choice). YAML form:
    //     options:
    //       - "Option A"
    //       - "Option B"
    const opts: string[] = [];
    const optMatch = entry.match(/options:\s*\n((?:\s{2,}-\s*.*\n?)+)/);
    if (optMatch) {
      for (const line of optMatch[1].split("\n")) {
        const m = line.match(/^\s*-\s*["']?(.*?)["']?\s*$/);
        if (m && m[1].trim()) opts.push(m[1].trim());
      }
    }
    out.push({ text, why, options: opts.length > 0 ? opts : undefined });
  }
  return out;
}


function statusElapsedSeconds(startedAt: string | null | undefined): number {
  if (!startedAt) return 0;
  return Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000));
}

/**
 * Epic-only flow with persisted-clarification:
 *
 *   1. Clarify — kicked off automatically when the epic was created. Survives
 *      tab close / navigation; we just poll the DB row for progress and load
 *      whatever's there when you return. User can "re-clarify" to start fresh.
 *   2. Decompose — architect produces a feature/task tree, fed by the Q&A.
 *   3. Approve — server parses the (possibly edited) YAML and creates rows.
 *
 * Tasks are materialized but NOT auto-started.
 */
export function DecomposePanel({
  externalId,
  onApproved,
  alreadyDecomposed = false,
  childCount = 0,
}: {
  externalId: string;
  onApproved: () => void;
  /** Epic already has features. We collapse the panel by default so the
   * roadmap is the primary surface; user can expand to append more. */
  alreadyDecomposed?: boolean;
  childCount?: number;
}) {
  const qc = useQueryClient();

  // ---- Phase 1: persisted clarification (poll while running) ----
  const clarifyQ = useQuery({
    queryKey: ["clarification", externalId],
    queryFn: () => api.getClarification(externalId),
    // Poll fast while in flight; idle when done/failed/null so we don't burn
    // requests on a finished epic.
    refetchInterval: (q) => {
      const d = q.state.data;
      return d && (d.status === "running" || d.status === "pending") ? 1500 : false;
    },
  });
  const clarification = clarifyQ.data ?? null;

  const [answers, setAnswers] = useState<string[]>([]);
  const [clarifySkipped, setClarifySkipped] = useState(false);

  // Derive the "current" questions list — partial parse while streaming,
  // authoritative once done.
  const liveQuestions: ClarificationQuestion[] =
    clarification?.status === "done"
      ? clarification.questions
      : clarification
        ? partialParseQuestions(clarification.raw_md || "")
        : [];

  // Grow answers to match question count without clobbering already-typed values.
  useEffect(() => {
    const n = liveQuestions.length;
    setAnswers((prev) => {
      if (prev.length === n) return prev;
      if (prev.length < n) return [...prev, ...Array(n - prev.length).fill("")];
      return prev.slice(0, n);
    });
  }, [liveQuestions.length]);

  // ---- Phase 2: decompose (backgrounded — survives tab close) ----
  // Polled persistent draft. While status=running, raw_md streams + tool-use
  // progress markers update the UI. When status=done, we seed the editor.
  const draftQ = useQuery({
    queryKey: ["decompose-draft", externalId],
    queryFn: () => api.getDecomposeDraft(externalId),
    refetchInterval: (q) => {
      const d = q.state.data;
      return d && (d.status === "running" || d.status === "pending") ? 1500 : false;
    },
  });
  const draft = draftQ.data ?? null;
  const draftDone = draft?.status === "done";
  const draftRunning =
    draft?.status === "running" || draft?.status === "pending";

  // Editor state — only seeded once when the draft transitions to done so
  // subsequent polls don't clobber user edits.
  const [editedMd, setEditedMd] = useState<string>("");
  const [seededDraftId, setSeededDraftId] = useState<number | null>(null);
  useEffect(() => {
    if (draftDone && draft && draft.id !== seededDraftId) {
      setEditedMd(draft.raw_md || "");
      setSeededDraftId(draft.id);
    }
  }, [draftDone, draft, seededDraftId]);

  const [error, setError] = useState<string | null>(null);

  const kickMut = useMutation({
    mutationFn: () => api.kickClarification(externalId),
    onSuccess: (row) => {
      setError(null);
      setAnswers([]);
      setClarifySkipped(false);
      qc.setQueryData(["clarification", externalId], row);
    },
    onError: (e: Error) => setError(e.message),
  });

  const draftMut = useMutation({
    mutationFn: () => {
      const qa: ClarificationQA[] =
        liveQuestions.length > 0 && !clarifySkipped
          ? liveQuestions.map((q, i) => ({
              question: q.text,
              why: q.why,
              answer: answers[i] || "",
            }))
          : [];
      return api.decomposeDraft(externalId, qa);
    },
    onSuccess: (row) => {
      // Server returned the fresh draft row (status=pending/running). Seed
      // the cache so polling picks up where it left off without a refetch.
      qc.setQueryData(["decompose-draft", externalId], row);
      setSeededDraftId(null); // editor will re-seed when this draft completes
      setEditedMd("");
      setError(null);
    },
    onError: (e: Error) => setError(e.message),
  });

  const approveMut = useMutation({
    mutationFn: () => api.decomposeApprove(externalId, { raw_md: editedMd }),
    onSuccess: (res) => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["children", externalId] });
      qc.invalidateQueries({ queryKey: ["ticket", externalId] });
      qc.invalidateQueries({ queryKey: ["tickets"] });
      qc.invalidateQueries({ queryKey: ["roadmap", externalId] });
      onApproved();
      setEditedMd("");
      setSeededDraftId(null);
      setAnswers([]);
      setClarifySkipped(false);
      console.info(
        `decompose approved: ${res.feature_external_ids.length} features, ${res.task_external_ids.length} tasks`,
      );
    },
    onError: (e: Error) => setError(e.message),
  });

  const isRunning =
    clarification?.status === "running" || clarification?.status === "pending";

  // Once features exist, collapse the whole panel so the roadmap is the
  // primary surface. User can re-expand to append more features. (Approving
  // a new decomposition APPENDS to existing children, doesn't replace them.)
  const Container = alreadyDecomposed
    ? ({ children }: { children: React.ReactNode }) => (
        <details className="rounded-2xl border border-purple-400/15 bg-purple-400/[0.02] open:bg-purple-400/[0.03] open:border-purple-400/20 transition group">
          <summary className="cursor-pointer list-none px-4 py-3 flex items-center justify-between gap-3 select-none">
            <div className="flex items-center gap-2 text-sm">
              <span className="inline-block transition-transform group-open:rotate-90 text-neutral-500">
                ›
              </span>
              <span className="text-[11px] uppercase tracking-[0.14em] font-semibold text-purple-200">
                decompose with architect
              </span>
              <span className="text-xs text-neutral-500">
                already decomposed — {childCount} feature{childCount === 1 ? "" : "s"} below.
                Expand to add more.
              </span>
            </div>
          </summary>
          <div className="px-4 pb-4 flex flex-col gap-3">{children}</div>
        </details>
      )
    : ({ children }: { children: React.ReactNode }) => (
        <section className="rounded-2xl border border-purple-400/20 bg-purple-400/[0.03] p-4 flex flex-col gap-3">
          {children}
        </section>
      );

  return (
    <Container>
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          {!alreadyDecomposed ? (
            <h2 className="text-[11px] uppercase tracking-[0.14em] font-semibold text-purple-200">
              decompose epic
            </h2>
          ) : null}
          <p className="text-xs text-neutral-500 mt-1 leading-relaxed">
            {alreadyDecomposed
              ? "Approving a new decomposition appends features to the roadmap. To start over, delete the existing features first."
              : "The architect's clarifying questions run in the background — they keep going even if you close the tab. Answer (or skip) what makes sense, then decompose."}
          </p>
        </div>
        <div className="flex gap-2 items-center">
          {clarification ? (
            <button
              onClick={() => kickMut.mutate()}
              disabled={kickMut.isPending || isRunning}
              className="px-3 py-1.5 rounded-full bg-white/5 hover:bg-white/10 border border-purple-400/30 text-purple-100 text-sm font-medium disabled:opacity-40 transition"
              title={isRunning ? "wait for the current run to finish" : ""}
            >
              {kickMut.isPending ? "kicking…" : "re-clarify"}
            </button>
          ) : null}
          {!draft ? (
            <button
              onClick={() => draftMut.mutate()}
              disabled={draftMut.isPending || isRunning}
              className="px-3 py-1.5 rounded-full bg-purple-500 hover:bg-purple-400 text-white text-sm font-medium shadow-lg shadow-purple-500/25 disabled:opacity-40 disabled:shadow-none transition tabular-nums"
              title={isRunning ? "wait for clarify to finish (or skip below)" : ""}
            >
              {draftMut.isPending ? "drafting…" : "draft decomposition"}
            </button>
          ) : (
            <button
              onClick={() => draftMut.mutate()}
              disabled={draftMut.isPending}
              className="px-3 py-1.5 rounded-full bg-white/5 hover:bg-white/10 border border-purple-400/30 text-purple-100 text-sm font-medium disabled:opacity-40 transition"
            >
              {draftMut.isPending ? "drafting…" : "re-draft"}
            </button>
          )}
        </div>
      </div>

      {error ? (
        <div className="rounded-xl border border-rose-400/20 bg-rose-400/[0.06] text-rose-300 px-3 py-2 text-sm">
          {error}
        </div>
      ) : null}
      {clarification?.error ? (
        <div className="rounded-xl border border-rose-400/20 bg-rose-400/[0.06] text-rose-300 px-3 py-2 text-sm">
          clarify error: {clarification.error}
        </div>
      ) : null}

      {/* Phase 1: clarifying questions display (only if we have a clarification). */}
      {clarification && !draft ? (
        <ClarifyView
          clarification={clarification}
          liveQuestions={liveQuestions}
          answers={answers}
          onAnswerChange={(i, v) =>
            setAnswers((prev) => prev.map((a, j) => (j === i ? v : a)))
          }
          onDraft={() => {
            setClarifySkipped(false);
            draftMut.mutate();
          }}
          onSkipQA={() => {
            setClarifySkipped(true);
            draftMut.mutate();
          }}
          isDrafting={draftMut.isPending}
        />
      ) : null}

      {/* No clarification yet (rare — only for epics created before auto-clarify) */}
      {!clarifyQ.isLoading && !clarification && !draft ? (
        <div className="rounded-xl border border-white/10 bg-white/[0.02] px-4 py-3 text-sm text-neutral-300">
          No clarification yet for this epic. Kick one off, or jump straight to
          decompose.
          <div className="mt-3 flex gap-2">
            <button
              onClick={() => kickMut.mutate()}
              disabled={kickMut.isPending}
              className="px-3 py-1.5 rounded-full bg-purple-500 hover:bg-purple-400 text-white text-sm font-medium shadow-lg shadow-purple-500/25 disabled:opacity-40 transition"
            >
              {kickMut.isPending ? "kicking…" : "ask clarifying questions"}
            </button>
          </div>
        </div>
      ) : null}

      {/* Phase 2: decompose draft — backgrounded, polled, tab-resilient */}
      {draft ? (
        <DecomposeDraftView
          draft={draft}
          isRunning={draftRunning}
          editedMd={editedMd}
          onEditedMdChange={setEditedMd}
          onApprove={() => approveMut.mutate()}
          onDiscard={() => {
            // Clear local editor state but leave the persisted draft so the
            // user can re-open the same tab and pick up where they left off.
            setEditedMd("");
            setSeededDraftId(null);
            setError(null);
          }}
          isApproving={approveMut.isPending}
        />
      ) : null}
    </Container>
  );
}

function ClarifyView({
  clarification,
  liveQuestions,
  answers,
  onAnswerChange,
  onDraft,
  onSkipQA,
  isDrafting,
}: {
  clarification: PersistedClarification;
  liveQuestions: ClarificationQuestion[];
  answers: string[];
  onAnswerChange: (i: number, v: string) => void;
  onDraft: () => void;
  onSkipQA: () => void;
  isDrafting: boolean;
}) {
  const isRunning =
    clarification.status === "running" || clarification.status === "pending";
  const isDone = clarification.status === "done";
  const totalSoFar = liveQuestions.length;
  const answeredCount = answers.filter((a) => (a || "").trim().length > 0).length;

  // For the "elapsed" indicator while running we use the persisted started_at.
  // Tick once a second so the number visibly increments even between polls.
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!isRunning) return;
    const id = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [isRunning]);
  // Use tick to ensure rerender (variable referenced).
  void tick;
  const elapsed = statusElapsedSeconds(clarification.started_at);

  if (isDone && liveQuestions.length === 0) {
    return (
      <div className="rounded-xl border border-white/10 bg-white/[0.02] px-4 py-3 text-sm text-neutral-300">
        Architect had nothing worth asking — body is clear enough. Proceed to decompose.
        <div className="mt-3">
          <button
            onClick={onSkipQA}
            disabled={isDrafting}
            className="px-3 py-1.5 rounded-full bg-purple-500 hover:bg-purple-400 text-white text-sm font-medium shadow-lg shadow-purple-500/25 disabled:opacity-40 transition"
          >
            {isDrafting ? "drafting…" : "draft decomposition"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="text-xs text-neutral-500 font-mono tabular-nums flex items-center gap-2 flex-wrap">
        {isRunning ? (
          <span className="inline-flex items-center gap-1.5 text-purple-200">
            <span className="size-1.5 rounded-full bg-purple-300 animate-pulse" />
            architect typing… {elapsed}s
          </span>
        ) : null}
        {isDone ? (
          <span>
            clarify · {clarification.num_turns ?? "?"} turns ·{" "}
            {clarification.duration_ms != null
              ? `${(clarification.duration_ms / 1000).toFixed(1)}s`
              : "?"}
             · ${(clarification.total_cost_usd ?? 0).toFixed(4)}
          </span>
        ) : null}
        {clarification.status === "failed" ? (
          <span className="text-rose-400">failed: {clarification.error}</span>
        ) : null}
        {totalSoFar > 0 ? (
          <span className="text-neutral-300">
            · {answeredCount}/{totalSoFar} answered
          </span>
        ) : null}
      </div>

      {totalSoFar === 0 && isRunning ? (
        <ProgressFeed
          events={extractProgress(clarification.raw_md || "")}
          emptyHint="waiting for the first question…"
        />
      ) : null}

      <ol className="flex flex-col gap-3">
        {liveQuestions.map((q, i) => (
          <li
            key={i}
            className="rounded-xl border border-white/10 bg-white/[0.02] px-4 py-3"
          >
            <div className="text-sm text-neutral-100 font-medium">
              Q{i + 1}. {q.text}
            </div>
            {q.why ? (
              <div className="text-xs text-neutral-500 mt-1 italic">{q.why}</div>
            ) : null}
            <QuestionAnswer
              question={q}
              value={answers[i] ?? ""}
              onChange={(v) => onAnswerChange(i, v)}
            />
          </li>
        ))}
      </ol>

      <div className="flex items-center gap-2 flex-wrap">
        <button
          onClick={onDraft}
          disabled={isDrafting || isRunning || totalSoFar === 0}
          className="px-4 py-2 rounded-full bg-purple-500 hover:bg-purple-400 text-white text-sm font-medium shadow-lg shadow-purple-500/25 disabled:opacity-40 transition"
          title={
            isRunning
              ? "wait for the architect to finish, or use skip"
              : totalSoFar === 0
                ? "no questions parsed yet"
                : ""
          }
        >
          {isDrafting ? "drafting…" : "decompose with these answers"}
        </button>
        <button
          onClick={onSkipQA}
          disabled={isDrafting}
          className="px-3 py-2 rounded-full bg-white/5 hover:bg-white/10 border border-white/10 text-sm disabled:opacity-40 transition"
        >
          {isRunning ? "skip & decompose without answers" : "skip — let architect assume"}
        </button>
      </div>
    </div>
  );
}

function DecomposeDraftView({
  draft,
  isRunning,
  editedMd,
  onEditedMdChange,
  onApprove,
  onDiscard,
  isApproving,
}: {
  draft: AgentDraft;
  isRunning: boolean;
  editedMd: string;
  onEditedMdChange: (v: string) => void;
  onApprove: () => void;
  onDiscard: () => void;
  isApproving: boolean;
}) {
  const features = draftFeatures(draft);
  const events = extractProgress(draft.raw_md || "");

  // Tick once a second so the elapsed timer increments between polls.
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!isRunning) return;
    const id = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [isRunning]);
  void tick;
  const elapsed = statusElapsedSeconds(draft.started_at);

  // While running we hide the editor (the user can't edit a still-streaming
  // draft) and show the live raw text + activity feed. When done, switch to
  // the side-by-side editor + preview.
  if (isRunning) {
    return (
      <>
        <div className="text-xs font-mono text-purple-200 flex items-center gap-2">
          <span className="size-1.5 rounded-full bg-purple-300 animate-pulse" />
          architect drafting… {elapsed}s
          <span className="text-neutral-500">
            (tab-safe — closing this page won't cancel the run)
          </span>
        </div>
        <ProgressFeed events={events} emptyHint="warming up…" />
        {draft.raw_md ? (
          <div className="rounded-xl border border-white/10 bg-white/[0.02] p-3 max-h-72 overflow-auto markdown-body text-sm">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {stripProgressMarkers(draft.raw_md)}
            </ReactMarkdown>
          </div>
        ) : null}
      </>
    );
  }

  return (
    <>
      <div className="text-xs text-neutral-500 font-mono">
        architect · {draft.num_turns ?? "?"} turns ·{" "}
        {draft.duration_ms != null
          ? `${(draft.duration_ms / 1000).toFixed(1)}s`
          : "?"}{" "}
        · ${(draft.total_cost_usd ?? 0).toFixed(4)}
        {draft.status === "failed" ? (
          <span className="text-rose-400 ml-2"> · failed: {draft.error}</span>
        ) : null}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div className="flex flex-col">
          <label className="text-[11px] uppercase tracking-[0.14em] text-neutral-500 mb-1">
            markdown (editable)
          </label>
          <textarea
            value={editedMd}
            onChange={(e) => onEditedMdChange(e.target.value)}
            spellCheck={false}
            className="flex-1 min-h-[420px] w-full p-3 font-mono text-sm border border-white/10 rounded-xl bg-white/[0.03] text-neutral-100 focus:outline-none focus:border-purple-400/40 transition"
          />
        </div>
        <div className="flex flex-col">
          <label className="text-[11px] uppercase tracking-[0.14em] text-neutral-500 mb-1">
            preview
          </label>
          <div className="flex-1 min-h-[420px] w-full p-3 overflow-auto border border-white/10 rounded-xl bg-white/[0.02] markdown-body text-sm">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{editedMd || "_empty_"}</ReactMarkdown>
          </div>
        </div>
      </div>

      {features.length > 0 ? (
        <details className="rounded-xl border border-white/10 bg-white/[0.02] px-3 py-2 text-sm">
          <summary className="cursor-pointer text-neutral-300">
            parsed: {features.length} feature
            {features.length === 1 ? "" : "s"},{" "}
            {features.reduce((n, f) => n + f.tasks.length, 0)} tasks
          </summary>
          <ul className="mt-2 ml-4 list-disc space-y-1 text-neutral-300">
            {features.map((f, i) => (
              <li key={i}>
                <span className="font-medium">{f.title}</span>
                {f.domain ? (
                  <span className="text-xs text-neutral-500 ml-2 font-mono">
                    [{f.domain}]
                  </span>
                ) : null}
                <ul className="ml-5 list-[circle] mt-1 space-y-0.5 text-neutral-400 text-xs">
                  {f.tasks.map((t, j) => (
                    <li key={j}>{t.title}</li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <div className="flex items-center gap-3 flex-wrap">
        <button
          onClick={onApprove}
          disabled={!editedMd.trim() || isApproving}
          className="px-4 py-2 rounded-full bg-emerald-500 hover:bg-emerald-400 text-white text-sm font-medium shadow-lg shadow-emerald-500/25 disabled:opacity-40 transition"
        >
          {isApproving ? "creating…" : "approve & create children"}
        </button>
        <button
          onClick={onDiscard}
          disabled={isApproving}
          className="px-3 py-2 rounded-full bg-white/5 hover:bg-white/10 border border-white/10 text-sm disabled:opacity-40 transition"
        >
          discard
        </button>
      </div>
    </>
  );
}

/** Live feed of "what the architect is doing" — tool-use events the backend
 * embeds as comment markers in raw_md while clarify is still running. */
function ProgressFeed({
  events,
  emptyHint,
}: {
  events: ProgressEvent[];
  emptyHint: string;
}) {
  if (events.length === 0) {
    return (
      <div className="rounded-xl border border-white/10 bg-white/[0.02] px-4 py-3 text-sm text-neutral-400 italic">
        <span className="inline-flex items-center gap-2">
          <span className="size-1.5 rounded-full bg-purple-300 animate-pulse" />
          {emptyHint}
        </span>
      </div>
    );
  }
  // Keep the feed compact — show the most recent 6 actions.
  const recent = events.slice(-6);
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] px-4 py-3 text-sm">
      <div className="text-[11px] uppercase tracking-[0.14em] text-purple-200/80 mb-2">
        architect activity
      </div>
      <ul className="flex flex-col gap-1 font-mono text-xs text-neutral-300">
        {recent.map((e, i) => (
          <li key={i} className="flex items-center gap-2">
            <span className="text-neutral-500 w-16 shrink-0">
              {TOOL_LABEL[e.tool] ?? e.tool.toLowerCase()}
            </span>
            <span className="truncate">{e.summary}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Per-question answer widget — radio buttons when the architect supplied
 * `options`, free-text fallback when it didn't. When options are present,
 * we still expose a small "other (write in)" affordance so the user isn't
 * forced into a preset choice. */
function QuestionAnswer({
  question,
  value,
  onChange,
}: {
  question: ClarificationQuestion;
  value: string;
  onChange: (v: string) => void;
}) {
  const hasOptions = !!question.options && question.options.length > 0;
  const matchesOption = hasOptions && question.options!.includes(value);
  const [otherOpen, setOtherOpen] = useState(false);

  if (!hasOptions) {
    return (
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="your answer (or leave blank to let the architect assume)"
        rows={2}
        className="mt-2 w-full px-3 py-2 rounded-lg bg-white/[0.03] border border-white/10 text-sm text-neutral-100 placeholder-neutral-600 focus:outline-none focus:border-purple-400/40 transition resize-none"
      />
    );
  }

  const showOther = otherOpen || (!matchesOption && value.length > 0);

  return (
    <div className="mt-2 flex flex-col gap-2">
      {question.options!.map((opt) => {
        const selected = value === opt;
        return (
          <label
            key={opt}
            className={`flex items-start gap-2 px-3 py-2 rounded-lg border text-sm cursor-pointer transition ${
              selected
                ? "border-purple-400/40 bg-purple-400/[0.08] text-neutral-100"
                : "border-white/10 bg-white/[0.02] text-neutral-300 hover:bg-white/[0.05]"
            }`}
          >
            <input
              type="radio"
              checked={selected}
              onChange={() => {
                onChange(opt);
                setOtherOpen(false);
              }}
              className="mt-0.5 accent-purple-400"
            />
            <span>{opt}</span>
          </label>
        );
      })}
      {!showOther ? (
        <button
          type="button"
          onClick={() => {
            setOtherOpen(true);
            onChange("");
          }}
          className="self-start text-xs text-purple-300 hover:text-purple-200 transition"
        >
          + write in another answer
        </button>
      ) : (
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="other answer…"
          rows={2}
          autoFocus
          className="w-full px-3 py-2 rounded-lg bg-white/[0.03] border border-white/10 text-sm text-neutral-100 placeholder-neutral-600 focus:outline-none focus:border-purple-400/40 transition resize-none"
        />
      )}
    </div>
  );
}

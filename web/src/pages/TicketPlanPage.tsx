import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { AgentDraft, api, StatusEvent, subscribeStatus, Ticket, TicketKind } from "../lib/api";
import { DecomposePanel } from "../components/DecomposePanel";
import { DependencyEditor } from "../components/DependencyEditor";
import { LiveRunPanel } from "../components/LiveRunPanel";
import { PlanEditor } from "../components/PlanEditor";
import { ChildStatusChips } from "../components/ChildStatusChips";
import { RoadmapView } from "../components/RoadmapView";
import { StartChildrenButton } from "../components/StartChildrenButton";
import { SubtreeActivityPanel } from "../components/SubtreeActivityPanel";
import { StatusBadge } from "../components/StatusBadge";
import { TicketInfoCard } from "../components/TicketInfoCard";
import {
  TOOL_LABEL,
  extractProgress,
  stripProgressMarkers,
} from "../lib/progressMarkers";

// Statuses that mean a dev agent run is or was happening — render LiveRunPanel.
const RUN_VISIBLE = new Set(["running_dev", "done", "in_progress", "pr_open", "failed"]);

// Plan workflow: terminal once the server stops streaming.
const TERMINAL_EXEC = new Set(["COMPLETED", "FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"]);

// What kind of child each level allows.
const CHILD_KIND: Record<TicketKind, TicketKind | null> = {
  epic: "feature",
  feature: "task",
  task: null,
};

export function TicketPlanPage() {
  const { externalId = "" } = useParams();
  const qc = useQueryClient();
  const [planContent, setPlanContent] = useState<string>("");
  // Track which AgentDraft.id has already seeded the editor — prevents
  // subsequent polls from clobbering the user's edits after the draft is done.
  const [seededDraftId, setSeededDraftId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Use a query-string override for domains.yaml that may not live in the target repo yet.
  const domainsFileOverride = useMemo(() => {
    const v = new URLSearchParams(location.search).get("domains_file");
    return v || undefined;
  }, []);

  const ticketQ = useQuery({
    queryKey: ["ticket", externalId],
    queryFn: () => api.getTicket(externalId),
    enabled: !!externalId,
  });

  const ticket = ticketQ.data;
  const isContainer = !!ticket && ticket.kind !== "task";
  const allowedChildKind = ticket ? CHILD_KIND[ticket.kind] : null;

  // Children list (epics + features have one; tasks don't).
  // No polling — re-fetches happen via queryClient.invalidateQueries after
  // a decompose-approve or a child create. Removed the 5s refetchInterval
  // because each call walks the subtree and the data only changes on
  // explicit user actions.
  const childrenQ = useQuery({
    queryKey: ["children", externalId],
    queryFn: () => api.listChildren(externalId),
    enabled: !!ticket && isContainer,
  });

  // Architect drafting — kicked off async, persisted to DB, polled here.
  // Closing the tab or navigating away does NOT cancel the run.
  const planDraftQ = useQuery({
    queryKey: ["plan-draft", externalId],
    queryFn: () => api.getPlanDraft(externalId),
    enabled: !!externalId && !!ticket && ticket.kind === "task",
    refetchInterval: (q) => {
      const d = q.state.data;
      return d && (d.status === "running" || d.status === "pending") ? 1500 : false;
    },
  });
  const planDraft = planDraftQ.data ?? null;
  const planDraftRunning =
    planDraft?.status === "running" || planDraft?.status === "pending";
  const planDraftDone = planDraft?.status === "done";

  // Seed the editor once when a fresh draft completes; subsequent polls of
  // the same draft must not stomp the user's edits.
  useEffect(() => {
    if (planDraftDone && planDraft && planDraft.id !== seededDraftId) {
      const payloadMd = (planDraft.payload as { plan_md?: string } | undefined)?.plan_md;
      setPlanContent(payloadMd || planDraft.raw_md || "");
      setSeededDraftId(planDraft.id);
    }
  }, [planDraftDone, planDraft, seededDraftId]);

  const draftMut = useMutation({
    mutationFn: () =>
      api.draftPlan(externalId, {
        domain_name: ticket?.domain_name || undefined,
        domains_file: domainsFileOverride,
      }),
    onSuccess: (row) => {
      // Seed the cache with the freshly-created row (status=pending/running)
      // so the polling query picks it up without an extra refetch.
      qc.setQueryData(["plan-draft", externalId], row);
      setSeededDraftId(null); // editor re-seeds when this draft completes
      setPlanContent("");
      setError(null);
    },
    onError: (e: Error) => setError(e.message),
  });

  const approveMut = useMutation({
    mutationFn: () =>
      api.approvePlan(externalId, {
        content_md: planContent,
        domain_name: ticket?.domain_name || "",
        domains_file: domainsFileOverride,
      }),
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["ticket", externalId] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const cancelMut = useMutation({
    mutationFn: () => api.cancel(externalId),
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["ticket", externalId] });
    },
    onError: (e: Error) => setError(e.message),
  });

  // For tasks materialized via epic decomposition: no workflow yet. The user
  // explicitly starts it from this page when they're ready to plan it.
  const startMut = useMutation({
    mutationFn: () => api.startWorkflow(externalId),
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["ticket", externalId] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const nav = useNavigate();
  const deleteMut = useMutation({
    mutationFn: () => api.deleteTicket(externalId),
    onSuccess: (res) => {
      setError(null);
      // Refresh every list view so the deleted subtree drops out everywhere.
      qc.invalidateQueries({ queryKey: ["tickets"] });
      qc.invalidateQueries({ queryKey: ["children"] });
      qc.invalidateQueries({ queryKey: ["roadmap"] });
      console.info(
        `deleted ${res.deleted_ticket_count} ticket(s), terminated ${res.workflows_terminated} workflow(s)`,
      );
      // Hop to the parent if there was one, else home.
      const parent = ticket?.parent_external_id;
      nav(parent ? `/tickets/${encodeURIComponent(parent)}` : "/", { replace: true });
    },
    onError: (e: Error) => setError(e.message),
  });

  function confirmDelete() {
    if (!ticket) return;
    const n = ticket.child_count;
    const detail =
      n > 0
        ? ` This will also delete ${n} direct ${n === 1 ? "child" : "children"} plus their entire subtree.`
        : "";
    const ok = window.confirm(
      `Delete ${ticket.kind} "${ticket.title}"?${detail} Active workflows will be terminated. This cannot be undone.`,
    );
    if (ok) deleteMut.mutate();
  }

  // Live workflow status via SSE — only for tasks (containers have no workflow).
  const [statusEvt, setStatusEvt] = useState<StatusEvent | null>(null);
  useEffect(() => {
    if (!ticket || ticket.kind !== "task") return;
    const off = subscribeStatus(externalId, {
      onStatus: (e) => setStatusEvt(e),
      onError: (msg) => setError((prev) => prev || msg),
    });
    return off;
  }, [externalId, ticket]);

  const workflowDone = !!statusEvt && TERMINAL_EXEC.has(statusEvt.execution_status);
  const waitingForPlan = statusEvt?.status === "waiting_for_plan";

  if (!externalId) return null;
  if (ticketQ.isLoading) return <Center>loading…</Center>;
  if (ticketQ.error || !ticket)
    return (
      <Center>
        <p className="text-rose-400">
          ticket error: {(ticketQ.error as Error | undefined)?.message || "not found"}
        </p>
        <Link to="/" className="text-blue-400 hover:underline">back home</Link>
      </Center>
    );

  return (
    <div className="max-w-6xl mx-auto px-6 sm:px-8 py-10 flex flex-col gap-6">
      <header className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-3">
            <Link to="/" className="text-xs text-neutral-500 hover:text-neutral-300 transition">← home</Link>
            <KindPill kind={ticket.kind} />
          </div>
          {ticket.parent_external_id ? (
            <ParentBreadcrumb externalId={ticket.parent_external_id} />
          ) : null}
          <h1 className="text-3xl font-semibold tracking-tight mt-2">{ticket.title}</h1>
          <div className="flex items-center gap-2 mt-2 flex-wrap">
            {ticket.github_issue_url ? (
              <a
                href={ticket.github_issue_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-blue-400/10 border border-blue-400/30 text-xs text-blue-200 hover:bg-blue-400/15 transition"
                title={ticket.github_issue_url}
              >
                ⎘ imported from GitHub issue
              </a>
            ) : null}
            {ticket.pr_url ? (
              <a
                href={ticket.pr_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-400/10 border border-emerald-400/30 text-xs text-emerald-200 hover:bg-emerald-400/15 transition"
                title={ticket.pr_url}
              >
                ⤴ PR #{ticket.pr_number} opened on GitHub
              </a>
            ) : null}
          </div>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          {ticket.kind === "task" ? (
            <>
              <StatusBadge status={statusEvt?.status || ticket.status} />
              {statusEvt?.execution_status ? (
                <StatusBadge status={statusEvt.execution_status} tone="execution" />
              ) : null}
            </>
          ) : (
            <>
              <StatusBadge status={ticket.status} />
              <ChildStatusChips ticket={ticket} size="lg" />
            </>
          )}
          <button
            onClick={confirmDelete}
            disabled={deleteMut.isPending}
            className="mt-2 text-[11px] text-neutral-500 hover:text-rose-300 transition disabled:opacity-40"
            title="delete this ticket and its descendants"
          >
            {deleteMut.isPending ? "deleting…" : "delete"}
          </button>
        </div>
      </header>

      <TicketInfoCard ticket={ticket} />

      {error ? (
        <div className="rounded-2xl border border-rose-400/20 bg-rose-400/[0.06] text-rose-300 px-4 py-3 text-sm">
          {error}
        </div>
      ) : null}

      {/* Epic-only: architect-driven decomposition into features + tasks. */}
      {ticket.kind === "epic" ? (
        <DecomposePanel
          externalId={externalId}
          onApproved={() => { /* react-query handles refetch */ }}
          alreadyDecomposed={ticket.child_count > 0}
          childCount={ticket.child_count}
        />
      ) : null}

      {/* Epic: roadmap (waves) replaces the flat children list. */}
      {ticket.kind === "epic" ? (
        <section className="flex flex-col gap-3">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <h2 className="text-[11px] uppercase tracking-[0.14em] font-semibold text-neutral-500">
              roadmap
            </h2>
            <div className="flex items-center gap-2">
              <StartChildrenButton
                externalId={ticket.external_id}
                parentKind="epic"
                pendingCount={ticket.child_status_counts?.pending ?? undefined}
              />
              <Link
                to={`/new?parent=${encodeURIComponent(ticket.external_id)}`}
                className="px-3 py-1.5 rounded-full bg-blue-500 hover:bg-blue-400 text-white text-xs font-medium shadow-lg shadow-blue-500/25 transition"
              >
                + new feature
              </Link>
            </div>
          </div>
          <RoadmapView epicExternalId={externalId} />
          <SubtreeActivityPanel
            externalId={ticket.external_id}
            parentKind="epic"
          />
        </section>
      ) : null}

      {/* Feature: still uses the flat container view for its tasks. */}
      {ticket.kind === "feature" ? (
        <>
          <DependencyEditor feature={ticket} />
          <div className="flex items-center justify-end">
            <StartChildrenButton
              externalId={ticket.external_id}
              parentKind="feature"
              pendingCount={ticket.child_status_counts?.pending ?? undefined}
            />
          </div>
          <ContainerView
            ticket={ticket}
            children={childrenQ.data ?? []}
            allowedChildKind={allowedChildKind}
          />
          <SubtreeActivityPanel
            externalId={ticket.external_id}
            parentKind="feature"
          />
        </>
      ) : null}

      {/* Task view: existing plan + dev flow. */}
      {!isContainer ? (
        <>
          {!ticket.workflow_id ? (
            <section className="rounded-2xl border border-blue-400/20 bg-blue-400/[0.04] px-4 py-3 flex items-center justify-between gap-3">
              <div>
                <h2 className="text-[11px] uppercase tracking-[0.14em] font-semibold text-blue-200">
                  not started yet
                </h2>
                <p className="text-xs text-neutral-400 mt-1">
                  This task was created via decomposition. Start its workflow to draft + approve a
                  plan.
                </p>
              </div>
              <button
                onClick={() => startMut.mutate()}
                disabled={startMut.isPending}
                className="px-4 py-2 rounded-full bg-blue-500 hover:bg-blue-400 text-white text-sm font-medium shadow-lg shadow-blue-500/25 disabled:opacity-40 transition shrink-0"
              >
                {startMut.isPending ? "starting…" : "start workflow"}
              </button>
            </section>
          ) : null}

          <section className="flex flex-col gap-3">
            <div className="flex items-center gap-3 flex-wrap">
              <button
                onClick={() => draftMut.mutate()}
                disabled={
                  draftMut.isPending ||
                  planDraftRunning ||
                  workflowDone ||
                  !waitingForPlan
                }
                className="px-3.5 py-1.5 rounded-full bg-blue-500 hover:bg-blue-400 text-white text-sm font-medium shadow-lg shadow-blue-500/20 disabled:opacity-40 disabled:shadow-none transition"
                title={
                  workflowDone
                    ? "workflow already finished"
                    : !waitingForPlan
                      ? `workflow status: ${statusEvt?.status || ticket.status}`
                      : planDraftRunning
                        ? "architect is already drafting — closing the tab won't cancel it"
                        : ""
                }
              >
                {draftMut.isPending || planDraftRunning
                  ? "drafting…"
                  : planContent
                    ? "re-draft"
                    : "draft plan with architect"}
              </button>
              {planDraftDone && planDraft ? (
                <span className="text-xs text-neutral-500 font-mono">
                  architect · {planDraft.num_turns ?? "?"} turns ·{" "}
                  {planDraft.duration_ms != null
                    ? `${(planDraft.duration_ms / 1000).toFixed(1)}s`
                    : "?"}{" "}
                  · ${(planDraft.total_cost_usd ?? 0).toFixed(4)}
                </span>
              ) : null}
            </div>

            {planDraftRunning && planDraft ? (
              <PlanDraftActivity draft={planDraft} />
            ) : null}

            <PlanEditor
              value={planContent}
              onChange={setPlanContent}
              disabled={draftMut.isPending || planDraftRunning || approveMut.isPending}
            />

            <div className="flex items-center gap-3 mt-2 flex-wrap">
              <button
                onClick={() => approveMut.mutate()}
                disabled={
                  !planContent.trim() ||
                  approveMut.isPending ||
                  !waitingForPlan ||
                  workflowDone
                }
                className="px-5 py-2.5 rounded-full bg-emerald-500 hover:bg-emerald-400 text-white text-sm font-medium shadow-lg shadow-emerald-500/25 disabled:opacity-40 disabled:shadow-none transition"
              >
                {approveMut.isPending ? "approving…" : "approve & signal workflow"}
              </button>
              <button
                onClick={() => cancelMut.mutate()}
                disabled={cancelMut.isPending || workflowDone}
                className="px-4 py-2.5 rounded-full bg-white/5 hover:bg-white/10 border border-white/10 text-sm font-medium disabled:opacity-40 transition"
              >
                cancel workflow
              </button>
              {approveMut.isSuccess ? (
                <span className="text-sm text-emerald-400">
                  ✓ plan #{approveMut.data.plan_id} signalled
                </span>
              ) : null}
            </div>
          </section>

          {RUN_VISIBLE.has(statusEvt?.status || ticket.status) ? (
            <LiveRunPanel externalId={externalId} />
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function KindPill({ kind }: { kind: TicketKind }) {
  const styles: Record<TicketKind, string> = {
    epic: "bg-purple-400/15 text-purple-200 border-purple-400/30",
    feature: "bg-blue-400/15 text-blue-200 border-blue-400/30",
    task: "bg-neutral-400/15 text-neutral-200 border-neutral-400/30",
  };
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] uppercase tracking-[0.14em] border ${styles[kind]}`}
    >
      {kind}
    </span>
  );
}

function ParentBreadcrumb({ externalId }: { externalId: string }) {
  const parentQ = useQuery({
    queryKey: ["ticket", externalId],
    queryFn: () => api.getTicket(externalId),
  });
  if (!parentQ.data) return null;
  return (
    <Link
      to={`/tickets/${encodeURIComponent(parentQ.data.external_id)}`}
      className="inline-block mt-2 text-xs text-neutral-500 hover:text-blue-300 transition"
    >
      ↑ {parentQ.data.kind} · {parentQ.data.title}{" "}
      <span className="font-mono opacity-60">({parentQ.data.external_id})</span>
    </Link>
  );
}

function ContainerView({
  ticket,
  children: kids,
  allowedChildKind,
}: {
  ticket: Ticket;
  children: Ticket[];
  allowedChildKind: TicketKind | null;
}) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="text-[11px] uppercase tracking-[0.14em] font-semibold text-neutral-500">
          {allowedChildKind ? `${allowedChildKind}s` : "children"} ({kids.length})
        </h2>
        {allowedChildKind ? (
          <Link
            to={`/new?parent=${encodeURIComponent(ticket.external_id)}`}
            className="px-3 py-1.5 rounded-full bg-blue-500 hover:bg-blue-400 text-white text-xs font-medium shadow-lg shadow-blue-500/25 transition"
          >
            + new {allowedChildKind}
          </Link>
        ) : null}
      </div>
      {kids.length === 0 ? (
        <p className="text-sm text-neutral-600 italic">
          No {allowedChildKind ?? "children"} yet. Use the button above to add one.
        </p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {kids.map((k) => (
            <li key={k.id}>
              <Link
                to={`/tickets/${encodeURIComponent(k.external_id)}`}
                className="flex items-center gap-3 border rounded-2xl px-4 py-3 transition border-white/10 bg-white/[0.03] hover:bg-white/[0.06] hover:border-white/15"
              >
                <KindPill kind={k.kind} />
                <div className="flex-1 min-w-0">
                  <div className="font-medium truncate text-neutral-100">{k.title}</div>
                  <div className="text-[11px] text-neutral-500 font-mono truncate mt-0.5">
                    {k.external_id} · {k.domain_name || "—"}
                    {k.child_count > 0 ? ` · ${k.child_count} child${k.child_count === 1 ? "" : "ren"}` : ""}
                  </div>
                </div>
                <StatusBadge status={k.status} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Center({ children }: { children: React.ReactNode }) {
  return <div className="min-h-screen flex flex-col items-center justify-center gap-2">{children}</div>;
}

/** Live "what the architect is doing" view, shown while a plan draft is
 * still streaming. Tab-resilient: the background task keeps writing even
 * if this view is closed; reopening picks up where it left off. */
function PlanDraftActivity({ draft }: { draft: AgentDraft }) {
  const events = extractProgress(draft.raw_md);
  const recent = events.slice(-6);

  // Tick once a second so the elapsed counter increments between polls.
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);
  void tick;
  const elapsed = draft.started_at
    ? Math.max(0, Math.floor((Date.now() - new Date(draft.started_at).getTime()) / 1000))
    : 0;

  return (
    <div className="rounded-2xl border border-blue-400/20 bg-blue-400/[0.04] p-4 flex flex-col gap-3">
      <div className="text-xs font-mono text-blue-200 flex items-center gap-2 flex-wrap">
        <span className="size-1.5 rounded-full bg-blue-300 animate-pulse" />
        architect drafting plan… {elapsed}s
        <span className="text-neutral-500">
          (tab-safe — closing this page won't cancel the run)
        </span>
      </div>
      {recent.length > 0 ? (
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
      ) : (
        <div className="text-xs text-neutral-500 italic">warming up…</div>
      )}
      {draft.raw_md ? (
        <div className="rounded-xl border border-white/10 bg-white/[0.02] p-3 max-h-60 overflow-auto markdown-body text-sm">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {stripProgressMarkers(draft.raw_md)}
          </ReactMarkdown>
        </div>
      ) : null}
    </div>
  );
}

// API client — talks to the FastAPI server at /api/*.
// In dev, Vite proxies /api → http://localhost:8765.

export interface Repo {
  id: number;
  name: string;
  local_path: string;
  github_owner: string | null;
  github_name: string | null;
}

export interface GitHubConnection {
  id: number;
  github_user_login: string;
  github_user_avatar_url: string | null;
  scopes: string | null;
  created_at: string;
}

export type TicketKind = "epic" | "feature" | "task";

export interface Ticket {
  id: number;
  external_id: string;
  title: string;
  body: string;
  domain_name: string | null;
  status: string;
  workflow_id: string | null;
  repo: Repo;
  kind: TicketKind;
  parent_external_id: string | null;
  child_count: number;
  // Null = inherit from ancestor or env default. See /cost-rollup for the
  // effective ceiling and which level binds.
  cost_ceiling_usd: number | null;
  created_at: string;
  updated_at: string;
  pr_number: number | null;
  pr_url: string | null;
  github_issue_url: string | null;
}

export interface BudgetBreakdown {
  ticket_id: number;
  external_id: string;
  kind: TicketKind;
  title: string;
  own_ceiling_usd: number | null;
  spent_usd: number;
  remaining_usd: number | null;
}

export interface CostRollup {
  ticket_id: number;
  external_id: string;
  kind: TicketKind;
  own_ceiling_usd: number | null;
  own_spent_usd: number;
  effective_remaining_usd: number | null;
  constraint_source: "self" | "feature" | "epic" | "env_default" | "unlimited";
  chain: BudgetBreakdown[];
}

export interface Domain {
  name: string;
  description: string;
  paths: string[];
  test: string | null;
  build: string | null;
}

export interface PlanDraft {
  plan_md: string;
  prompt_version: string;
  num_turns: number;
  duration_ms: number;
  total_cost_usd: number | null;
  domain_name: string;
}

export interface PlanApproveResult {
  plan_id: number;
  signalled: boolean;
  workflow_id: string;
}

export interface StatusEvent {
  workflow_id: string;
  status: string;
  execution_status: string;
  plan_id: number | null;
  at: string;
}

// One row in the /runs dashboard — a single dev/architect agent run.
// Metrics come from the run_finished event payload, so they're null while
// the run is in-flight.
export interface RunRow {
  id: number;
  ticket_id: number;
  ticket_external_id: string;
  ticket_title: string;
  repo_name: string;
  kind: string; // developer | architect | reviewer | tester
  status: string; // started | succeeded | failed | budget_exhausted
  started_at: string;
  ended_at: string | null;
  error: string | null;
  num_turns: number | null;
  duration_ms: number | null;
  total_cost_usd: number | null;
}

// One event on the live run stream. `kind` is one of the transcript entry
// kinds (assistant_text | tool_use | tool_result | system | result) or a
// lifecycle sentinel (run_started | run_finished).
export interface RunEvent {
  id: number;
  ticket_id: number;
  run_id: number | null;
  kind: string;
  message: string;
  payload: Record<string, unknown> | null;
  at: string;
}

async function jsonReq<T>(input: string, init?: RequestInit): Promise<T> {
  const r = await fetch(input, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers || {}) },
  });
  if (!r.ok) {
    let detail = `${r.status} ${r.statusText}`;
    try {
      const body = await r.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // ignore — non-JSON error body
    }
    throw new Error(detail);
  }
  return r.json() as Promise<T>;
}

export interface ClarificationQuestion {
  text: string;
  why: string;
  /** If present, render as multi-choice radios in addition to free-text. */
  options?: string[];
}

export interface ClarifyDraft {
  raw_md: string;
  questions: ClarificationQuestion[];
  prompt_version: string;
  num_turns: number;
  duration_ms: number;
  total_cost_usd: number | null;
  errors: string[];
}

export type ClarifyStatus = "pending" | "running" | "done" | "failed";

/** Persisted clarification row — what /api/tickets/{id}/clarification returns. */
export interface PersistedClarification {
  id: number;
  ticket_id: number;
  status: ClarifyStatus;
  raw_md: string;
  questions: ClarificationQuestion[];
  prompt_version: string | null;
  num_turns: number | null;
  duration_ms: number | null;
  total_cost_usd: number | null;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
}

export interface ClarificationQA {
  question: string;
  answer: string;
  why?: string;
}

export interface DecomposedTask {
  title: string;
  description: string;
}

export interface DecomposedFeature {
  title: string;
  description: string;
  domain: string | null;
  tasks: DecomposedTask[];
  depends_on: string[];
}

export interface RoadmapFeature {
  id: number;
  external_id: string;
  title: string;
  status: string;
  domain_name: string | null;
  workflow_id: string | null;
  child_count: number;
  prerequisite_external_ids: string[];
}

export interface RoadmapWave {
  index: number;
  features: RoadmapFeature[];
}

export interface Roadmap {
  epic_external_id: string;
  waves: RoadmapWave[];
  cyclic_external_ids: string[];
}

export interface DecomposeDraft {
  raw_md: string;
  features: DecomposedFeature[];
  prompt_version: string;
  num_turns: number;
  duration_ms: number;
  total_cost_usd: number | null;
  errors: string[];
}

export interface DecomposeApproveResult {
  feature_external_ids: string[];
  task_external_ids: string[];
}

/** Persisted architect-draft row (decompose or plan). The kickoff endpoints
 * return this immediately, and the GET endpoints are the UI's poll target.
 * `payload` shape varies by kind:
 *   - decompose: { features: DecomposedFeature[] }
 *   - plan:      { plan_md: string, domain_name: string } */
export type AgentDraftStatus = "pending" | "running" | "done" | "failed";

export interface AgentDraft {
  id: number;
  ticket_id: number;
  kind: "decompose" | "plan";
  status: AgentDraftStatus;
  raw_md: string;
  payload: Record<string, unknown>;
  prompt_version: string | null;
  num_turns: number | null;
  duration_ms: number | null;
  total_cost_usd: number | null;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
}

export interface GitHubRepoRef {
  owner: string;
  name: string;
  clone_url: string;
  default_branch?: string;
}

export interface GitHubIssueLabel {
  name: string;
  color: string | null;
}

export interface GitHubIssue {
  number: number;
  title: string;
  body: string;
  state: "open" | "closed";
  html_url: string | null;
  user_login: string | null;
  user_avatar_url: string | null;
  labels: GitHubIssueLabel[];
  comments: number;
  updated_at: string | null;
  created_at: string | null;
}

export interface GitHubIssueRef {
  owner: string;
  name: string;
  number: number;
  html_url?: string;
}

export interface GitHubRepoResult {
  owner: string;
  name: string;
  full_name: string;
  description: string | null;
  private: boolean;
  default_branch: string;
  clone_url: string | null;
  ssh_url: string | null;
  updated_at: string | null;
}

export interface CreateTicketInput {
  external_id?: string;
  title: string;
  body?: string;
  // Optional when parent_external_id is set — server inherits from parent.
  // Optional when `github_repo` is set — server lazily clones + registers.
  repo_path?: string;
  github_repo?: GitHubRepoRef;
  /** Source GitHub issue when imported via the /issues page. Server stamps
   * `github_issue_url` on the row and best-effort comments + labels the
   * issue ("pravi-imported"). */
  github_issue?: GitHubIssueRef;
  domain_name?: string;
  base_ref?: string;
  cleanup_worktree?: boolean;
  // Hierarchy.
  kind?: TicketKind;
  parent_external_id?: string;
  // Optional per-ticket spend cap. Null/omitted = inherit / unlimited.
  cost_ceiling_usd?: number | null;
}

export interface CreateTicketResult {
  external_id: string;
  ticket_id: number;
  // null for epics + features (no workflow today)
  workflow_id: string | null;
  web_url: string;
}

export const api = {
  listTickets: (
    opts: { status?: string; kind?: TicketKind; parent?: string; limit?: number } = {},
  ) => {
    const params = new URLSearchParams();
    if (opts.status) params.set("status", opts.status);
    if (opts.kind) params.set("kind", opts.kind);
    if (opts.parent) params.set("parent_external_id", opts.parent);
    if (opts.limit && opts.limit !== 100) params.set("limit", String(opts.limit));
    const qs = params.toString() ? `?${params}` : "";
    return jsonReq<Ticket[]>(`/api/tickets${qs}`);
  },

  listChildren: (externalId: string) =>
    jsonReq<Ticket[]>(`/api/tickets/${encodeURIComponent(externalId)}/children`),

  getRoadmap: (externalId: string) =>
    jsonReq<Roadmap>(`/api/tickets/${encodeURIComponent(externalId)}/roadmap`),

  addDependency: (externalId: string, prereqExternalId: string) =>
    jsonReq<{ id: number; created: boolean }>(
      `/api/tickets/${encodeURIComponent(externalId)}/dependencies`,
      {
        method: "POST",
        body: JSON.stringify({ prerequisite_external_id: prereqExternalId }),
      },
    ),

  deleteDependency: async (externalId: string, prereqExternalId: string) => {
    // 204 No Content — jsonReq would try to JSON.parse the empty body.
    const r = await fetch(
      `/api/tickets/${encodeURIComponent(externalId)}/dependencies/${encodeURIComponent(prereqExternalId)}`,
      { method: "DELETE" },
    );
    if (!r.ok) {
      let detail = `${r.status} ${r.statusText}`;
      try {
        const body = await r.json();
        if (body?.detail) detail = body.detail;
      } catch {
        /* empty body */
      }
      throw new Error(detail);
    }
  },

  clarify: (externalId: string) =>
    jsonReq<ClarifyDraft>(`/api/tickets/${encodeURIComponent(externalId)}/clarify`, {
      method: "POST",
      body: "{}",
    }),

  // The persisted, background-kicked clarification. GET returns the latest
  // (null if none exists). POST kicks off a fresh run, returning the new row.
  getClarification: (externalId: string) =>
    jsonReq<PersistedClarification | null>(
      `/api/tickets/${encodeURIComponent(externalId)}/clarification`,
    ),
  kickClarification: (externalId: string) =>
    jsonReq<PersistedClarification>(
      `/api/tickets/${encodeURIComponent(externalId)}/clarification`,
      { method: "POST", body: "{}" },
    ),

  // Streaming variant — see subscribeClarify() below for the typed wrapper.
  clarifyStreamUrl: (externalId: string) =>
    `/api/tickets/${encodeURIComponent(externalId)}/clarify/stream`,

  /** Kick off a backgrounded decompose draft. Returns the persisted row
   * immediately; poll `getDecomposeDraft` for updates. Survives tab close. */
  decomposeDraft: (externalId: string, clarifications: ClarificationQA[] = []) =>
    jsonReq<AgentDraft>(
      `/api/tickets/${encodeURIComponent(externalId)}/decompose/draft`,
      { method: "POST", body: JSON.stringify({ clarifications }) },
    ),

  getDecomposeDraft: (externalId: string) =>
    jsonReq<AgentDraft | null>(
      `/api/tickets/${encodeURIComponent(externalId)}/decompose-draft`,
    ),

  decomposeApprove: (externalId: string, body: { raw_md: string; approver?: string }) =>
    jsonReq<DecomposeApproveResult>(
      `/api/tickets/${encodeURIComponent(externalId)}/decompose/approve`,
      { method: "POST", body: JSON.stringify(body) },
    ),

  deleteTicket: (externalId: string) =>
    jsonReq<{ deleted_ticket_count: number; workflows_terminated: number }>(
      `/api/tickets/${encodeURIComponent(externalId)}`,
      { method: "DELETE" },
    ),

  // ---- GitHub auth ----
  /** Hard-redirect target — call via `window.location.href = api.githubLoginUrl()`. */
  githubLoginUrl: () => "/api/auth/github/login",

  githubMe: () => jsonReq<GitHubConnection | null>("/api/auth/github/me"),

  githubLogout: () =>
    jsonReq<{ revoked: boolean }>("/api/auth/github/logout", {
      method: "POST",
      body: "{}",
    }),

  /** Search the connected user's GitHub repos. Empty `q` returns the most-
   * recently-pushed repos (handy default while the input is blank). */
  searchGithubRepos: (q: string) => {
    const qs = q ? `?q=${encodeURIComponent(q)}` : "";
    return jsonReq<GitHubRepoResult[]>(`/api/auth/github/repos/search${qs}`);
  },

  /** List issues on a connected GitHub repo (PRs filtered out server-side). */
  listGithubIssues: (
    owner: string,
    name: string,
    opts: { state?: "open" | "closed" | "all"; labels?: string } = {},
  ) => {
    const params = new URLSearchParams();
    if (opts.state) params.set("state", opts.state);
    if (opts.labels) params.set("labels", opts.labels);
    const qs = params.toString() ? `?${params}` : "";
    return jsonReq<GitHubIssue[]>(
      `/api/auth/github/repos/${encodeURIComponent(owner)}/${encodeURIComponent(name)}/issues${qs}`,
    );
  },

  bulkDeleteTickets: (externalIds: string[]) =>
    jsonReq<{
      deleted_root_external_ids: string[];
      not_found_external_ids: string[];
      deleted_ticket_count: number;
      workflows_terminated: number;
    }>("/api/tickets/bulk-delete", {
      method: "POST",
      body: JSON.stringify({ external_ids: externalIds }),
    }),

  startWorkflow: (externalId: string, baseRef = "main") =>
    jsonReq<CreateTicketResult>(
      `/api/tickets/${encodeURIComponent(externalId)}/start-workflow?base_ref=${encodeURIComponent(baseRef)}`,
      { method: "POST", body: "{}" },
    ),

  createTicket: (input: CreateTicketInput) =>
    jsonReq<CreateTicketResult>("/api/tickets", {
      method: "POST",
      body: JSON.stringify(input),
    }),

  listRepos: () => jsonReq<Repo[]>("/api/repos"),

  listDomainsForPath: (repoPath: string, domainsFile?: string) => {
    const params = new URLSearchParams({ repo_path: repoPath });
    if (domainsFile) params.set("domains_file", domainsFile);
    return jsonReq<Domain[]>(`/api/repos/_/domains?${params}`);
  },

  getTicket: (externalId: string) =>
    jsonReq<Ticket>(`/api/tickets/${encodeURIComponent(externalId)}`),

  listDomains: (externalId: string, domainsFile?: string) => {
    const qs = domainsFile ? `?domains_file=${encodeURIComponent(domainsFile)}` : "";
    return jsonReq<Domain[]>(`/api/tickets/${encodeURIComponent(externalId)}/domains${qs}`);
  },

  /** Kick off a backgrounded plan draft for a task. Returns persisted row
   * immediately; poll `getPlanDraft` for progress + streamed `raw_md`. */
  draftPlan: (externalId: string, body: { domain_name?: string; domains_file?: string }) =>
    jsonReq<AgentDraft>(`/api/tickets/${encodeURIComponent(externalId)}/plan/draft`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getPlanDraft: (externalId: string) =>
    jsonReq<AgentDraft | null>(
      `/api/tickets/${encodeURIComponent(externalId)}/plan-draft`,
    ),

  approvePlan: (
    externalId: string,
    body: {
      content_md: string;
      domain_name: string;
      approver?: string;
      domains_file?: string;
    },
  ) =>
    jsonReq<PlanApproveResult>(`/api/tickets/${encodeURIComponent(externalId)}/plan/approve`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  cancel: (externalId: string) =>
    jsonReq<{ signalled: boolean; workflow_id: string }>(
      `/api/tickets/${encodeURIComponent(externalId)}/cancel`,
      { method: "POST", body: "{}" },
    ),

  listRuns: (limit = 100) => jsonReq<RunRow[]>(`/api/runs?limit=${limit}`),

  costRollup: (externalId: string) =>
    jsonReq<CostRollup>(`/api/tickets/${encodeURIComponent(externalId)}/cost-rollup`),

  // PATCH the per-ticket ceiling. Pass null to clear (revert to inheritance).
  updateBudget: (externalId: string, cost_ceiling_usd: number | null) =>
    jsonReq<Ticket>(`/api/tickets/${encodeURIComponent(externalId)}/budget`, {
      method: "PATCH",
      body: JSON.stringify({ cost_ceiling_usd }),
    }),
};

/**
 * Stream the architect's clarify response as it's produced. Fires `onText`
 * with each incremental chunk so the UI can render questions live (parsed
 * heuristically out of the partial YAML), then `onDone` once the server has
 * the authoritative structured result. Returns a cleanup function.
 */
export function subscribeClarify(
  externalId: string,
  handlers: {
    onText: (delta: string) => void;
    onDone: (final: ClarifyDraft) => void;
    onError?: (msg: string) => void;
  },
): () => void {
  const es = new EventSource(api.clarifyStreamUrl(externalId));
  es.addEventListener("text", (ev) => {
    try {
      const { delta } = JSON.parse((ev as MessageEvent).data) as { delta: string };
      if (delta) handlers.onText(delta);
    } catch (err) {
      handlers.onError?.(String(err));
    }
  });
  es.addEventListener("done", (ev) => {
    try {
      const final = JSON.parse((ev as MessageEvent).data) as ClarifyDraft;
      handlers.onDone(final);
    } catch (err) {
      handlers.onError?.(String(err));
    } finally {
      es.close();
    }
  });
  es.addEventListener("error", (ev) => {
    // `error` event fires both for our server-sent "error" events AND for
    // transport-level disconnects. We dispatch on whether `data` is parseable.
    const raw = (ev as MessageEvent).data;
    if (raw) {
      try {
        const { detail } = JSON.parse(raw) as { detail: string };
        handlers.onError?.(detail);
      } catch {
        handlers.onError?.(String(raw));
      }
    } else {
      handlers.onError?.("connection lost");
    }
    es.close();
  });
  return () => es.close();
}

// SSE wrapper for the live run stream — same shape as subscribeStatus.
export function subscribeRun(
  externalId: string,
  handlers: {
    onEvent: (e: RunEvent) => void;
    onClose?: () => void;
    onError?: (msg: string) => void;
  },
): () => void {
  const url = `/api/tickets/${encodeURIComponent(externalId)}/run/stream`;
  const es = new EventSource(url);
  es.addEventListener("run", (ev) => {
    try {
      const data = JSON.parse((ev as MessageEvent).data) as RunEvent;
      handlers.onEvent(data);
    } catch (err) {
      handlers.onError?.(String(err));
    }
  });
  es.addEventListener("close", () => {
    handlers.onClose?.();
    es.close();
  });
  es.addEventListener("error", () => {
    handlers.onError?.("connection lost");
  });
  return () => es.close();
}

// SSE wrapper: caller passes onStatus / onClose / onError; returns a cleanup fn.
export function subscribeStatus(
  externalId: string,
  handlers: {
    onStatus: (e: StatusEvent) => void;
    onClose?: () => void;
    onError?: (msg: string) => void;
  },
): () => void {
  const url = `/api/tickets/${encodeURIComponent(externalId)}/status/stream`;
  const es = new EventSource(url);
  es.addEventListener("status", (ev) => {
    try {
      const data = JSON.parse((ev as MessageEvent).data) as StatusEvent;
      handlers.onStatus(data);
    } catch (err) {
      handlers.onError?.(String(err));
    }
  });
  es.addEventListener("close", () => {
    handlers.onClose?.();
    es.close();
  });
  es.addEventListener("error", () => {
    handlers.onError?.("connection lost");
    // Let the browser auto-reconnect by default; only close on terminal events.
  });
  return () => es.close();
}

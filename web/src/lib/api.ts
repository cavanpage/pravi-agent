// API client — talks to the FastAPI server at /api/*.
// In dev, Vite proxies /api → http://localhost:8765.

export interface Repo {
  id: number;
  name: string;
  /** Nullable since ADR 0003 — local repos populated lazily by the
   * sandbox on first dev run. New repos start with `null` here. */
  local_path: string | null;
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
  /** ADR 0004 — what kind of work this is. Null = `other` (generic). */
  persona: string | null;
  /** ADR 0004 — what tech stack. Null = `unknown` (no extra skills hint). */
  stack: string | null;
  /** For feature + epic tickets: count of descendant TASKs grouped by
   * status (pending, planning, plan_approved, in_progress, pr_open,
   * merged, failed, cancelled). Empty for tasks. The server derives the
   * parent's `status` field from this same breakdown — these counts let
   * the UI also show the underlying mix. */
  child_status_counts: Record<string, number>;
}

export type PersonaStatus = "active" | "coming_soon";

export interface Persona {
  slug: string;
  name: string;
  group: string;
  status: PersonaStatus;
  description: string;
  baseline_skills: string[];
}

export interface Stack {
  slug: string;
  name: string;
  additional_skills: string[];
}

/** Per-persona / per-stack spend rollup row (ADR 0004 FinOps slice).
 * `persona` / `stack` are slugs; null on the ticket aggregates under
 * `other` / `unknown` server-side. */
export interface PersonaSpend {
  persona: string;
  spent_usd: number;
  run_count: number;
  ticket_count: number;
}

export interface StackSpend {
  stack: string;
  spent_usd: number;
  run_count: number;
  ticket_count: number;
}

export type SpendWindow = "7d" | "30d" | "all";

export interface StartChildrenSkipped {
  external_id: string;
  title: string;
  reason: string;
}

/** Outcome of `POST /tickets/{id}/start-children`. With `dry_run=true`,
 * `started` is the list that *would* be launched. Without dry_run, it's
 * the list that actually was. `skipped` includes tasks blocked by feature
 * dependencies, tasks already running, and any per-task launch failures. */
export interface StartChildrenResult {
  parent_external_id: string;
  parent_kind: "epic" | "feature" | "task";
  dry_run: boolean;
  started: string[];
  skipped: StartChildrenSkipped[];
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
  /** Populated by the subtree stream — null for per-task stream (consumer
   * already knows the ticket). Used by the subtree feed to tag events
   * with which task emitted them. */
  ticket_external_id: string | null;
  ticket_title: string | null;
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
  persona?: string | null;
  stack?: string | null;
}

export interface DecomposedFeature {
  title: string;
  description: string;
  domain: string | null;
  tasks: DecomposedTask[];
  depends_on: string[];
  persona?: string | null;
  stack?: string | null;
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

export interface CreateRepoInput {
  name: string;
  description?: string;
  private?: boolean;
  template?: "vite-react-static";
  deploy_to_cloudflare_pages?: boolean;
  register_in_pravi?: boolean;
}

export interface PagesProject {
  name: string;
  subdomain: string;
  pages_url: string;
  canonical_url: string | null;
}

export interface CreateRepoResult {
  repo: GitHubRepoResult;
  initial_commit_pushed: boolean;
  pages: PagesProject | null;
  pages_skipped_reason: string | null;
  pravi_repo_id: number | null;
}

export interface CloudflareAccountOption {
  id: string;
  name: string;
}

export interface CloudflareConnection {
  id: number;
  account_id: string;
  account_name: string | null;
  token_id: string | null;
  created_at: string;
}

/** Shape of the 409 response body when the pasted token can see >1
 * accounts and the modal needs to render an account picker. */
export interface CloudflareAccountPickerRequired {
  kind: "account_picker_required";
  message: string;
  accounts: CloudflareAccountOption[];
}

/** Thrown by `api.cloudflareConnect` when the server returns 409 with
 * the account-picker payload. The modal narrows on `instanceof` and
 * swaps the form for the picker UI without a second round-trip. */
export class CloudflareAccountPickerError extends Error {
  accounts: CloudflareAccountOption[];
  constructor(message: string, accounts: CloudflareAccountOption[]) {
    super(message);
    this.name = "CloudflareAccountPickerError";
    this.accounts = accounts;
  }
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
  /** GitHub's count of open issues + PRs (single field, includes both). */
  open_issues_count: number;
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
  // ADR 0004 — agent framing. Both nullable; null persona = `other`;
  // null stack = `unknown`.
  persona?: string | null;
  stack?: string | null;
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

  integrations: () =>
    jsonReq<{ cloudflare: { configured: boolean }; github: { connected: boolean } }>(
      "/api/auth/github/integrations",
    ),

  /** Active Cloudflare connection, or null if not connected. */
  cloudflareMe: () =>
    jsonReq<CloudflareConnection | null>("/api/auth/cloudflare/me"),

  /** Verify and persist a Cloudflare API token. Returns the active
   * connection on success. On 409 (token sees multiple accounts and no
   * account_id was supplied), throws a `CloudflareAccountPickerError`
   * carrying the candidate list so the modal can render an account
   * picker without a second round-trip. */
  cloudflareConnect: async (
    body: { api_token: string; account_id?: string },
  ): Promise<CloudflareConnection> => {
    const r = await fetch("/api/auth/cloudflare/connect", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    if (r.status === 409) {
      const payload = (await r.json().catch(() => null)) as {
        detail?: CloudflareAccountPickerRequired;
      } | null;
      const detail = payload?.detail;
      if (detail?.kind === "account_picker_required") {
        throw new CloudflareAccountPickerError(detail.message, detail.accounts);
      }
    }
    if (!r.ok) {
      let msg = `${r.status} ${r.statusText}`;
      try {
        const body = await r.json();
        if (typeof body?.detail === "string") msg = body.detail;
      } catch {
        // ignore
      }
      throw new Error(msg);
    }
    return r.json() as Promise<CloudflareConnection>;
  },

  cloudflareDisconnect: () =>
    jsonReq<{ revoked: boolean }>("/api/auth/cloudflare/disconnect", {
      method: "POST",
      body: "{}",
    }),

  /** Create a brand-new GitHub repo, seed with a template, optionally
   * connect to Cloudflare Pages, and register in pravi. */
  createNewRepo: (body: CreateRepoInput) =>
    jsonReq<CreateRepoResult>("/api/auth/github/repos/new", {
      method: "POST",
      body: JSON.stringify(body),
    }),

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

  /** Full persona catalog — active + coming_soon. UI greys out the
   * coming_soon ones; the decompose architect only sees active. */
  listPersonas: () => jsonReq<Persona[]>("/api/personas"),

  listStacks: () => jsonReq<Stack[]>("/api/stacks"),

  /** Per-persona spend rollup. `window` defaults to "all"; pass "7d" or
   * "30d" to scope. `repoId` optionally filters to one repo. */
  spendByPersona: (window: SpendWindow = "all", repoId?: number) => {
    const params = new URLSearchParams({ window });
    if (repoId != null) params.set("repo_id", String(repoId));
    return jsonReq<PersonaSpend[]>(`/api/spend/by-persona?${params}`);
  },

  /** Batch-start workflows for eligible task descendants of a feature
   * or epic. Pass `dryRun=true` to preview which tasks would launch
   * (used to populate the confirmation modal). Skips the architect plan
   * step — review happens at PR time. */
  startChildren: (
    externalId: string,
    opts: { dryRun?: boolean; baseRef?: string } = {},
  ) => {
    const params = new URLSearchParams();
    if (opts.dryRun) params.set("dry_run", "true");
    if (opts.baseRef) params.set("base_ref", opts.baseRef);
    return jsonReq<StartChildrenResult>(
      `/api/tickets/${encodeURIComponent(externalId)}/start-children?${params}`,
      { method: "POST", body: "{}" },
    );
  },

  spendByStack: (window: SpendWindow = "all", repoId?: number) => {
    const params = new URLSearchParams({ window });
    if (repoId != null) params.set("repo_id", String(repoId));
    return jsonReq<StackSpend[]>(`/api/spend/by-stack?${params}`);
  },

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

/** SSE for the cross-task aggregated activity feed on epic + feature
 * pages. Events arrive tagged with `ticket_external_id` / `ticket_title`
 * so the UI can show which task emitted each one. Same protocol as
 * `subscribeRun` but the endpoint never auto-closes — caller decides
 * when to disconnect. */
export function subscribeSubtreeRun(
  externalId: string,
  handlers: {
    onEvent: (e: RunEvent) => void;
    onClose?: () => void;
    onError?: (msg: string) => void;
  },
  opts: { replay?: number } = {},
): () => void {
  const params = new URLSearchParams();
  if (opts.replay != null) params.set("replay", String(opts.replay));
  const qs = params.toString() ? `?${params}` : "";
  const url = `/api/tickets/${encodeURIComponent(externalId)}/run/subtree-stream${qs}`;
  const es = new EventSource(url);
  es.addEventListener("run", (ev) => {
    try {
      const data = JSON.parse((ev as MessageEvent).data) as RunEvent;
      handlers.onEvent(data);
    } catch (err) {
      handlers.onError?.(String(err));
    }
  });
  es.addEventListener("error", () => {
    handlers.onError?.("connection lost");
  });
  return () => {
    handlers.onClose?.();
    es.close();
  };
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

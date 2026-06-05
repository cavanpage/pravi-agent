import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";

import { api, GitHubRepoRef, GitHubRepoResult, TicketKind } from "../lib/api";
import { PersonaPicker } from "../components/PersonaPicker";
import { CreateRepoModal } from "../components/CreateRepoModal";

// Kind-specific guidance so the form makes sense for epics + features too.
const KIND_LABELS: Record<TicketKind, string> = {
  epic: "Epic",
  feature: "Feature",
  task: "Task",
};

const KIND_HINTS: Record<TicketKind, string> = {
  epic:
    "Top-level container. No workflow runs yet — group features under it. Domain is optional (epics can span domains).",
  feature:
    "Groups related tasks under an epic. No workflow runs yet — split into tasks to execute.",
  task: "Leaf unit. Launches a FeatureWorkflow: architect drafts a plan, you approve, dev agent executes.",
};

const ALLOWED_CHILD_KINDS: Record<TicketKind, TicketKind | null> = {
  epic: "feature",
  feature: "task",
  task: null,
};

export function NewTicketPage() {
  const nav = useNavigate();
  const [searchParams] = useSearchParams();

  // Hierarchy from URL: ?parent=<external_id>&kind=<epic|feature|task>
  const parentId = searchParams.get("parent") || undefined;
  const explicitKind = (searchParams.get("kind") as TicketKind | null) || null;

  const [externalId, setExternalId] = useState("");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  // When the user picks a repo from the GitHub search, we stash the
  // coordinates here. The sandbox provisions a working dir lazily on the
  // first dev run (see ADR 0003).
  const [githubRepo, setGithubRepo] = useState<GitHubRepoResult | null>(null);
  const [showCreateRepo, setShowCreateRepo] = useState(false);
  const [domainName, setDomainName] = useState("");
  const [baseRef, setBaseRef] = useState("main");
  // Persona + stack (ADR 0004). null = unassigned / generic.
  const [persona, setPersona] = useState<string | null>(null);
  const [stack, setStack] = useState<string | null>(null);
  // Optional cumulative spend cap. Empty = inherit from parent / env default.
  const [ceilingUsd, setCeilingUsd] = useState("");
  const [error, setError] = useState<string | null>(null);

  const personasQ = useQuery({
    queryKey: ["personas"],
    queryFn: () => api.listPersonas(),
    staleTime: 5 * 60_000,
  });
  const stacksQ = useQuery({
    queryKey: ["stacks"],
    queryFn: () => api.listStacks(),
    staleTime: 5 * 60_000,
  });

  // Load the parent if there is one — for inheritance + display.
  const parentQ = useQuery({
    queryKey: ["ticket", parentId],
    queryFn: () => api.getTicket(parentId!),
    enabled: !!parentId,
  });

  // Parent's cost rollup — used to clamp the cost-ceiling input so a child
  // can't promise more budget than the epic (or env default) actually has
  // left. Walks the full chain on the server.
  const parentRollupQ = useQuery({
    queryKey: ["cost-rollup", parentId],
    queryFn: () => api.costRollup(parentId!),
    enabled: !!parentId,
    staleTime: 10_000,
  });

  // Derive kind. If a parent is loaded, default to that parent's allowed child.
  // Otherwise honour ?kind=, else "task".
  const kind: TicketKind = useMemo(() => {
    if (parentQ.data) {
      return ALLOWED_CHILD_KINDS[parentQ.data.kind] || "task";
    }
    return explicitKind || "task";
  }, [parentQ.data, explicitKind]);

  // Once parent loads, seed the inherited domain. The repo identity is
  // implied by `parent_external_id` (server inherits) — no client-side
  // path threading needed under ADR 0003's sandbox seam.
  const inheritedFromParent = !!parentQ.data;
  useEffect(() => {
    if (parentQ.data?.domain_name) setDomainName(parentQ.data.domain_name);
    // Inherit persona + stack so children default to "same kind of work".
    if (parentQ.data?.persona) setPersona(parentQ.data.persona);
    if (parentQ.data?.stack) setStack(parentQ.data.stack);
  }, [parentQ.data]);

  // Whether the user has connected GitHub — drives the search picker.
  const ghMeQ = useQuery({
    queryKey: ["githubMe"],
    queryFn: () => api.githubMe(),
    enabled: !inheritedFromParent,
    staleTime: 60_000,
  });
  const githubConnected = !!ghMeQ.data;

  // Debounced search query for the GitHub repo picker. Empty string is a
  // valid query (returns most-recently-pushed repos as a starting point).
  const [ghSearch, setGhSearch] = useState("");
  const [ghSearchDebounced, setGhSearchDebounced] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setGhSearchDebounced(ghSearch), 250);
    return () => clearTimeout(t);
  }, [ghSearch]);

  const ghReposQ = useQuery({
    queryKey: ["githubRepos", ghSearchDebounced],
    queryFn: () => api.searchGithubRepos(ghSearchDebounced),
    enabled: githubConnected && !inheritedFromParent && !githubRepo,
    staleTime: 30_000,
  });

  // When user picks a GitHub repo, auto-fill base branch from its default.
  useEffect(() => {
    if (githubRepo && (!baseRef || baseRef === "main")) {
      setBaseRef(githubRepo.default_branch || "main");
    }
  }, [githubRepo, baseRef]);

  // Domains for the chosen repo. We can only read these from disk, so:
  //   - Inherited tickets: use the parent's resolved local path (cloned
  //     by an earlier sandbox provision or eager create-time clone).
  //   - Brand-new GitHub-picked tickets: skip the picker. Server defaults
  //     to the first domain in `domains.yaml`. A future endpoint could
  //     fetch the file via the GitHub Contents API to populate this.
  const domainsLookupPath = parentQ.data?.repo.local_path || "";
  const domainsQ = useQuery({
    queryKey: ["domains", domainsLookupPath],
    queryFn: () => api.listDomainsForPath(domainsLookupPath),
    enabled: !!domainsLookupPath,
    retry: 0,
  });

  useEffect(() => {
    if (!domainName && domainsQ.data && domainsQ.data.length > 0) {
      setDomainName(domainsQ.data[0].name);
    }
  }, [domainsQ.data, domainName]);

  useEffect(() => {
    if (
      domainName &&
      domainsQ.data &&
      !domainsQ.data.some((d) => d.name === domainName)
    ) {
      setDomainName(domainsQ.data[0]?.name || "");
    }
  }, [domainsQ.data, domainName]);

  const ceilingTrim = ceilingUsd.trim();
  const ceilingParsed = ceilingTrim === "" ? null : Number(ceilingTrim);
  const ceilingNegative =
    ceilingTrim !== "" && (Number.isNaN(ceilingParsed) || (ceilingParsed as number) < 0);
  // Parent's effective remaining caps the child — server enforces it at run
  // time but it's a better UX to refuse here than to land a phantom budget.
  const parentRemaining = parentRollupQ.data?.effective_remaining_usd ?? null;
  const ceilingExceedsParent =
    parentRemaining !== null &&
    ceilingParsed !== null &&
    !ceilingNegative &&
    (ceilingParsed as number) > parentRemaining;
  const ceilingInvalid = ceilingNegative || ceilingExceedsParent;

  const githubRepoRef: GitHubRepoRef | undefined = useMemo(() => {
    if (!githubRepo || !githubRepo.clone_url) return undefined;
    return {
      owner: githubRepo.owner,
      name: githubRepo.name,
      clone_url: githubRepo.clone_url,
      default_branch: githubRepo.default_branch,
    };
  }, [githubRepo]);

  const createMut = useMutation({
    mutationFn: () =>
      api.createTicket({
        external_id: externalId.trim() || undefined,
        title: title.trim(),
        body,
        // Send a local repo_path OR a github_repo coordinate. If a parent
        // is set, both are ignored and the server inherits from parent.
        // Repo identity comes from `parent_external_id` (inherits) or
        // `github_repo` (lazy clone). Local-path entry was removed by
        // ADR 0003 — the server resolves the working dir at run time.
        repo_path: undefined,
        github_repo: githubRepoRef,
        // Epics CAN have no domain; for features + tasks, send what's chosen.
        domain_name: kind === "epic" && !domainName ? undefined : domainName || undefined,
        base_ref: baseRef.trim() || "main",
        kind,
        parent_external_id: parentId,
        cost_ceiling_usd: ceilingParsed,
        // ADR 0004 — agent framing. Null on either falls back to
        // parent inherit (server-side), then catalog defaults.
        persona,
        stack,
      }),
    onSuccess: (res) => {
      setError(null);
      nav(`/tickets/${encodeURIComponent(res.external_id)}`);
    },
    onError: (e: Error) => setError(e.message),
  });

  const needsDomain = kind !== "epic";
  // Domain can only be picked when we have a local checkout to read
  // `domains.yaml` from — i.e. inherited from parent. For fresh GH-picked
  // tickets the server falls back to the first domain.
  const canSelectDomain = !!domainsLookupPath;
  const canSubmit =
    !!title.trim() &&
    (!!githubRepo || inheritedFromParent) &&
    (!needsDomain || !canSelectDomain || !!domainName) &&
    !ceilingInvalid &&
    !createMut.isPending;

  return (
    <div className="max-w-2xl mx-auto px-6 sm:px-8 py-12">
      <Link to="/" className="text-xs text-neutral-500 hover:text-neutral-300 transition">
        ← home
      </Link>
      <h1 className="text-3xl font-semibold tracking-tight mt-2">
        New {KIND_LABELS[kind].toLowerCase()}
      </h1>

      {parentQ.data ? (
        <div className="mt-4 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-sm">
          <div className="text-[11px] uppercase tracking-[0.14em] text-neutral-500">
            child of {parentQ.data.kind}
          </div>
          <Link
            to={`/tickets/${encodeURIComponent(parentQ.data.external_id)}`}
            className="text-neutral-200 hover:text-blue-300 transition"
          >
            {parentQ.data.title}{" "}
            <span className="text-neutral-500 font-mono text-xs">
              ({parentQ.data.external_id})
            </span>
          </Link>
          <p className="text-xs text-neutral-500 mt-1">
            Repo, domain, and base branch are inherited from the parent. The architect will see
            the parent's body merged into the prompt.
          </p>
        </div>
      ) : null}

      <p className="text-neutral-400 text-sm mt-3 leading-relaxed">{KIND_HINTS[kind]}</p>

      {error ? (
        <div className="mt-5 rounded-2xl border border-rose-400/20 bg-rose-400/[0.06] text-rose-300 px-4 py-3 text-sm">
          {error}
        </div>
      ) : null}

      <form
        className="mt-8 grid grid-cols-1 gap-5"
        onSubmit={(e) => {
          e.preventDefault();
          if (canSubmit) createMut.mutate();
        }}
      >
        <Field
          label="External ID"
          hint={`Leave blank to auto-generate (${kind === "epic" ? "e" : kind === "feature" ? "f" : "t"}-xxxxxxxx).`}
        >
          <input
            type="text"
            value={externalId}
            onChange={(e) => setExternalId(e.target.value)}
            placeholder="optional"
            className={inputClasses}
          />
        </Field>

        <Field label="Title" hint="One line.">
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            className={inputClasses}
          />
        </Field>

        <Field
          label="Description"
          hint={
            inheritedFromParent
              ? "Just the delta — the parent's body is merged into the architect's prompt automatically."
              : kind === "task"
                ? "Markdown OK. Be concrete — the architect bases the plan on this."
                : "Markdown OK. Describes what this container groups; not used by an agent yet."
          }
        >
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={6}
            className={`${inputClasses} font-mono text-sm resize-none`}
          />
        </Field>

        <Field
          label="Repo"
          hint={
            inheritedFromParent
              ? "Inherited from parent."
              : githubRepo
                ? "Picked from GitHub. The sandbox lazily clones it on first dev run (see ADR 0003)."
                : githubConnected
                  ? "Search your GitHub repos. Pravi identifies repos by their GitHub coordinates — the local clone is a sandbox detail."
                  : "Connect GitHub from the home page header to pick a repo."
          }
        >
          {inheritedFromParent && parentQ.data ? (
            <div className="rounded-xl border border-white/10 bg-white/[0.02] px-3.5 py-2.5 text-sm text-neutral-300">
              {parentQ.data.repo.github_owner && parentQ.data.repo.github_name
                ? `${parentQ.data.repo.github_owner}/${parentQ.data.repo.github_name}`
                : parentQ.data.repo.name}
            </div>
          ) : githubRepo ? (
            <SelectedGithubRepo
              repo={githubRepo}
              onClear={() => setGhSearch("")}
              onReplace={() => setGithubRepo(null)}
            />
          ) : githubConnected ? (
            <GitHubRepoPicker
              query={ghSearch}
              onQueryChange={setGhSearch}
              repos={ghReposQ.data ?? []}
              isLoading={ghReposQ.isFetching}
              error={(ghReposQ.error as Error | null)?.message ?? null}
              onPick={(r) => setGithubRepo(r)}
              onCreateNew={() => setShowCreateRepo(true)}
            />
          ) : (
            <div className="rounded-xl border border-amber-400/20 bg-amber-400/[0.04] px-3.5 py-2.5 text-sm text-amber-200">
              Connect GitHub first — pravi identifies repos by their GitHub
              coordinates.
            </div>
          )}
        </Field>

        {needsDomain ? (
          <Field
            label="Domain"
            hint={
              domainsQ.isError
                ? `loading domains failed: ${(domainsQ.error as Error).message}`
                : inheritedFromParent
                  ? "Default inherited from parent — override if this child belongs to a different domain."
                  : "The server uses the first domain in `domains.yaml` after the sandbox provisions. You can change it later."
            }
          >
            {canSelectDomain ? (
              <select
                value={domainName}
                onChange={(e) => setDomainName(e.target.value)}
                disabled={!domainsQ.data}
                className={inputClasses}
              >
                {!domainsQ.data ? (
                  <option value="">loading…</option>
                ) : null}
                {domainsQ.data?.map((d) => (
                  <option key={d.name} value={d.name}>
                    {d.name} {d.description ? `— ${d.description}` : ""}
                  </option>
                ))}
              </select>
            ) : (
              <div className="rounded-xl border border-white/10 bg-white/[0.02] px-3.5 py-2.5 text-sm text-neutral-400 italic">
                Auto-selected at first dev run (no local checkout yet).
              </div>
            )}
          </Field>
        ) : null}

        <Field
          label="Persona"
          hint={
            inheritedFromParent
              ? "Defaults to the parent's persona; override only if this child does a different kind of work."
              : "Optional — shapes the dev agent's framing. The 6 active personas are pickable; coming-soon ones are disabled. See ADR 0004."
          }
        >
          <PersonaPicker
            value={persona}
            onChange={setPersona}
            personas={personasQ.data ?? null}
          />
        </Field>

        <Field
          label="Stack"
          hint="What tech this ticket is in (e.g. python-fastapi, typescript-react). Determines which Claude Skills get hinted. Leave unassigned for generic."
        >
          <select
            value={stack ?? ""}
            onChange={(e) => setStack(e.target.value || null)}
            className={inputClasses}
            disabled={!stacksQ.data}
          >
            <option value="">— unassigned (generic) —</option>
            {stacksQ.data?.map((s) =>
              s.slug === "unknown" ? null : (
                <option key={s.slug} value={s.slug}>
                  {s.name}
                </option>
              ),
            )}
          </select>
        </Field>

        {kind === "task" ? (
          <Field label="Base branch">
            <input
              type="text"
              value={baseRef}
              onChange={(e) => setBaseRef(e.target.value)}
              className={inputClasses}
            />
          </Field>
        ) : null}

        <Field
          label="Cost ceiling (USD)"
          hint={
            inheritedFromParent && parentRemaining !== null
              ? `Parent has $${parentRemaining.toFixed(2)} remaining. Leave blank to inherit; set a number to give this child its own (tighter) cap.`
              : inheritedFromParent
                ? "Leave blank to inherit from the parent. Set a number to give this child its own cap."
                : kind === "task"
                  ? "Optional cumulative cap across all runs of this task. Blank = env default / unlimited."
                  : "Optional cap across every descendant task. Lets you bound an entire epic/feature."
          }
        >
          <input
            type="text"
            inputMode="decimal"
            value={ceilingUsd}
            onChange={(e) => setCeilingUsd(e.target.value)}
            placeholder={
              parentRemaining !== null ? `≤ ${parentRemaining.toFixed(2)}` : "e.g. 20"
            }
            className={`${inputClasses} tabular-nums ${
              ceilingInvalid ? "border-rose-400/40" : ""
            }`}
          />
          {ceilingNegative ? (
            <span className="block text-xs text-rose-400 mt-1.5">
              Must be a non-negative number, or blank.
            </span>
          ) : ceilingExceedsParent ? (
            <span className="block text-xs text-rose-400 mt-1.5">
              Parent has ${parentRemaining!.toFixed(2)} remaining — child can't exceed that.
            </span>
          ) : null}
        </Field>

        <div className="flex gap-4 items-center mt-2">
          <button
            type="submit"
            disabled={!canSubmit}
            className="px-5 py-2.5 rounded-full bg-blue-500 hover:bg-blue-400 text-white text-sm font-medium shadow-lg shadow-blue-500/25 disabled:opacity-40 disabled:shadow-none transition"
          >
            {createMut.isPending
              ? "creating…"
              : kind === "task"
                ? "create & start workflow"
                : `create ${KIND_LABELS[kind].toLowerCase()}`}
          </button>
          <Link to="/" className="text-sm text-neutral-400 hover:text-neutral-200 transition">
            cancel
          </Link>
        </div>
      </form>

      {showCreateRepo ? (
        <CreateRepoModal
          onClose={() => setShowCreateRepo(false)}
          onPicked={(r) => {
            setGithubRepo(r);
            setShowCreateRepo(false);
          }}
        />
      ) : null}
    </div>
  );
}

const inputClasses =
  "w-full px-3.5 py-2.5 rounded-xl bg-white/[0.03] border border-white/10 text-sm text-neutral-100 placeholder-neutral-600 focus:outline-none focus:border-blue-400/40 focus:bg-white/[0.05] disabled:opacity-60 transition";

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="block text-[11px] uppercase tracking-[0.14em] font-semibold text-neutral-400 mb-1.5">
        {label}
      </span>
      {children}
      {hint ? (
        <span className="block text-xs text-neutral-500 mt-1.5 leading-relaxed">{hint}</span>
      ) : null}
    </label>
  );
}

function SelectedGithubRepo({
  repo,
  onClear,
  onReplace,
}: {
  repo: GitHubRepoResult;
  onClear: () => void;
  onReplace: () => void;
}) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-emerald-400/25 bg-emerald-400/[0.06] px-3.5 py-2.5">
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="currentColor"
        className="size-5 text-emerald-300 shrink-0 mt-0.5"
        aria-hidden="true"
      >
        <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.38 7.86 10.9.58.1.79-.25.79-.55v-2c-3.2.7-3.88-1.36-3.88-1.36-.52-1.33-1.28-1.69-1.28-1.69-1.05-.72.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.71 1.26 3.37.96.1-.74.4-1.26.73-1.55-2.55-.29-5.24-1.28-5.24-5.7 0-1.26.45-2.29 1.19-3.1-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.18 1.18a11 11 0 0 1 5.78 0c2.21-1.49 3.18-1.18 3.18-1.18.63 1.59.23 2.76.11 3.05.74.81 1.19 1.84 1.19 3.1 0 4.43-2.7 5.41-5.27 5.69.41.36.78 1.06.78 2.14v3.17c0 .31.21.66.8.55C20.22 21.37 23.5 17.08 23.5 12 23.5 5.65 18.35.5 12 .5Z" />
      </svg>
      <div className="flex-1 min-w-0">
        <div className="text-sm text-neutral-100 font-medium truncate">
          {repo.full_name}
          {repo.private ? (
            <span className="ml-2 text-[10px] uppercase tracking-[0.14em] text-amber-300/80 align-middle">
              private
            </span>
          ) : null}
        </div>
        {repo.description ? (
          <div className="text-xs text-neutral-400 truncate mt-0.5">
            {repo.description}
          </div>
        ) : null}
        <div className="text-[11px] text-neutral-500 font-mono mt-0.5">
          default: {repo.default_branch}
        </div>
      </div>
      <button
        type="button"
        onClick={() => {
          onReplace();
          onClear();
        }}
        className="text-xs text-neutral-400 hover:text-neutral-200 px-2 py-1 rounded-full bg-white/5 hover:bg-white/10 border border-white/10 transition shrink-0"
      >
        change
      </button>
    </div>
  );
}

function GitHubRepoPicker({
  query,
  onQueryChange,
  repos,
  isLoading,
  error,
  onPick,
  onCreateNew,
}: {
  query: string;
  onQueryChange: (q: string) => void;
  repos: GitHubRepoResult[];
  isLoading: boolean;
  error: string | null;
  onPick: (r: GitHubRepoResult) => void;
  onCreateNew: () => void;
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-white/5">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="size-4 text-neutral-500 shrink-0"
          aria-hidden="true"
        >
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-3.5-3.5" />
        </svg>
        <input
          type="text"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          placeholder="search your github repos…"
          className="flex-1 bg-transparent text-sm text-neutral-100 placeholder-neutral-600 focus:outline-none py-1"
          aria-label="search github repos"
        />
        {isLoading ? (
          <span className="text-[11px] text-neutral-500">searching…</span>
        ) : null}
        <button
          type="button"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onCreateNew();
          }}
          className="text-[11px] px-2 py-1 rounded-full bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-200 border border-emerald-400/30 transition shrink-0"
          title="Scaffold a new repo from a template — auto-deploys to Cloudflare Pages if configured"
        >
          + new repo
        </button>
      </div>
      {error ? (
        <div className="px-3 py-2 text-xs text-rose-300">{error}</div>
      ) : repos.length === 0 ? (
        <div className="px-3 py-3 text-xs text-neutral-500 italic">
          {isLoading ? "loading…" : "no repos match — try a different query."}
        </div>
      ) : (
        <ul className="max-h-72 overflow-y-auto divide-y divide-white/5">
          {repos.map((r) => (
            <li key={r.full_name}>
              <button
                type="button"
                // Picker lives inside a `<Field>` (= `<label>`); label
                // click semantics try to focus the first form control
                // inside, which can race with our state update on
                // some browsers. Stop the click from bubbling so the
                // pick is the *only* thing that happens.
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  onPick(r);
                }}
                className="w-full text-left px-3 py-2 hover:bg-white/[0.04] transition flex items-start gap-3"
              >
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-neutral-100 truncate">
                    {r.full_name}
                    {r.private ? (
                      <span className="ml-2 text-[10px] uppercase tracking-[0.14em] text-amber-300/70 align-middle">
                        private
                      </span>
                    ) : null}
                  </div>
                  {r.description ? (
                    <div className="text-xs text-neutral-500 truncate mt-0.5">
                      {r.description}
                    </div>
                  ) : null}
                </div>
                <div className="flex flex-col items-end gap-0.5 shrink-0 self-center">
                  {r.open_issues_count > 0 ? (
                    <span
                      className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full border border-emerald-400/30 bg-emerald-400/[0.08] text-emerald-200"
                      title={`${r.open_issues_count} open issues/PRs on GitHub — single field, includes both`}
                    >
                      <svg viewBox="0 0 16 16" width="10" height="10" fill="currentColor" aria-hidden="true">
                        <path d="M8 9.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Z" />
                        <path
                          fillRule="evenodd"
                          d="M8 0a8 8 0 1 1 0 16A8 8 0 0 1 8 0Zm0 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13Z"
                        />
                      </svg>
                      {r.open_issues_count}
                    </span>
                  ) : null}
                  <span className="text-[11px] text-neutral-500 font-mono">
                    {r.default_branch}
                  </span>
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

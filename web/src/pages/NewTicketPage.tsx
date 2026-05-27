import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";

import { api, GitHubRepoRef, GitHubRepoResult, TicketKind } from "../lib/api";

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
  const [repoPath, setRepoPath] = useState("");
  // When the user picks a repo from the GitHub search, we stash the
  // coordinates here. The server clones lazily at create time so the path
  // doesn't have to exist locally yet — repoPath stays empty until then.
  const [githubRepo, setGithubRepo] = useState<GitHubRepoResult | null>(null);
  const [domainName, setDomainName] = useState("");
  const [baseRef, setBaseRef] = useState("main");
  // Optional cumulative spend cap. Empty = inherit from parent / env default.
  const [ceilingUsd, setCeilingUsd] = useState("");
  const [error, setError] = useState<string | null>(null);

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

  // Once parent loads, lock + seed repo/domain.
  const inheritedFromParent = !!parentQ.data;
  useEffect(() => {
    if (parentQ.data) {
      setRepoPath(parentQ.data.repo.local_path);
      if (parentQ.data.domain_name) setDomainName(parentQ.data.domain_name);
    }
  }, [parentQ.data]);

  const reposQ = useQuery({
    queryKey: ["repos"],
    queryFn: () => api.listRepos(),
    enabled: !inheritedFromParent,
  });

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

  useEffect(() => {
    if (!inheritedFromParent && !repoPath && reposQ.data && reposQ.data.length > 0) {
      setRepoPath(reposQ.data[0].local_path);
    }
  }, [reposQ.data, repoPath, inheritedFromParent]);

  // Domains for the chosen repo. Always shown, even with inherited repo, so
  // users can override (e.g. feature in a different domain than the epic).
  const domainsQ = useQuery({
    queryKey: ["domains", repoPath],
    queryFn: () => api.listDomainsForPath(repoPath),
    enabled: !!repoPath,
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
        repo_path: githubRepoRef ? undefined : repoPath || undefined,
        github_repo: githubRepoRef,
        // Epics CAN have no domain; for features + tasks, send what's chosen.
        domain_name: kind === "epic" && !domainName ? undefined : domainName || undefined,
        base_ref: baseRef.trim() || "main",
        kind,
        parent_external_id: parentId,
        cost_ceiling_usd: ceilingParsed,
      }),
    onSuccess: (res) => {
      setError(null);
      nav(`/tickets/${encodeURIComponent(res.external_id)}`);
    },
    onError: (e: Error) => setError(e.message),
  });

  const needsDomain = kind !== "epic";
  // Domains can only be loaded from a *local* checkout, so they're only
  // required when we already have one. Picking from GitHub defers the
  // domain choice (epic is fine with none; for feature/task the user
  // needs to clone or seed domains.yaml separately — out of scope here).
  const canSelectDomain = !!repoPath && !githubRepo;
  const canSubmit =
    !!title.trim() &&
    (!!repoPath || !!githubRepo || inheritedFromParent) &&
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
                ? "Picked from GitHub. The server clones it on create if not already cached."
                : githubConnected
                  ? "Search your GitHub repos, or paste a local path below."
                  : "Connect GitHub on the home page to search your repos, or paste a local path."
          }
        >
          {githubRepo ? (
            <SelectedGithubRepo
              repo={githubRepo}
              onClear={() => setGhSearch("")}
              onReplace={() => setGithubRepo(null)}
            />
          ) : !inheritedFromParent && githubConnected ? (
            <GitHubRepoPicker
              query={ghSearch}
              onQueryChange={setGhSearch}
              repos={ghReposQ.data ?? []}
              isLoading={ghReposQ.isFetching}
              error={(ghReposQ.error as Error | null)?.message ?? null}
              onPick={(r) => {
                setGithubRepo(r);
                setRepoPath("");
              }}
            />
          ) : null}

          {!inheritedFromParent && !githubRepo && reposQ.data && reposQ.data.length > 0 ? (
            <select
              value={repoPath}
              onChange={(e) => setRepoPath(e.target.value)}
              className={`${inputClasses} ${githubConnected ? "mt-3" : ""}`}
            >
              <option value="">
                {githubConnected
                  ? "— or pick from local repos —"
                  : "— pick a local repo —"}
              </option>
              {reposQ.data.map((r) => (
                <option key={r.local_path} value={r.local_path}>
                  {r.name} ({r.local_path})
                </option>
              ))}
            </select>
          ) : null}
          {!githubRepo ? (
            <input
              type="text"
              value={repoPath}
              onChange={(e) => setRepoPath(e.target.value)}
              placeholder="/absolute/path/to/repo"
              disabled={inheritedFromParent}
              className={`${inputClasses} mt-2 font-mono text-sm`}
            />
          ) : null}
        </Field>

        {needsDomain ? (
          <Field
            label="Domain"
            hint={
              domainsQ.isError
                ? `loading domains failed: ${(domainsQ.error as Error).message}`
                : inheritedFromParent
                  ? "Default inherited from parent — override if this child belongs to a different domain."
                  : "Pick which slice of the repo the dev agent is scoped to."
            }
          >
            <select
              value={domainName}
              onChange={(e) => setDomainName(e.target.value)}
              disabled={!domainsQ.data}
              className={inputClasses}
            >
              {!domainsQ.data ? (
                <option value="">{repoPath ? "loading…" : "select a repo first"}</option>
              ) : null}
              {domainsQ.data?.map((d) => (
                <option key={d.name} value={d.name}>
                  {d.name} {d.description ? `— ${d.description}` : ""}
                </option>
              ))}
            </select>
          </Field>
        ) : null}

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
}: {
  query: string;
  onQueryChange: (q: string) => void;
  repos: GitHubRepoResult[];
  isLoading: boolean;
  error: string | null;
  onPick: (r: GitHubRepoResult) => void;
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
                onClick={() => onPick(r)}
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
                <span className="text-[11px] text-neutral-500 font-mono shrink-0 self-center">
                  {r.default_branch}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

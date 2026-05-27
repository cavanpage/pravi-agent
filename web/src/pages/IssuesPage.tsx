import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  api,
  GitHubIssue,
  GitHubIssueRef,
  GitHubRepoResult,
  TicketKind,
} from "../lib/api";
import { useIssuesViewState, IssueState } from "../lib/useIssuesViewState";

/** /issues page — browse GitHub issues on a connected repo and import them
 * as pravi epics / features / tasks. State (selected repo + filters) lives
 * in localStorage so picking up where you left off is the default. */
export function IssuesPage() {
  const { state: view, setRepo, setState, setLabels, setSearch } =
    useIssuesViewState();
  const { repo, state, labels, search } = view;
  const [importing, setImporting] = useState<GitHubIssue | null>(null);

  // GitHub auth state. The page requires a connection — show a stub
  // otherwise.
  const ghMeQ = useQuery({
    queryKey: ["githubMe"],
    queryFn: () => api.githubMe(),
    staleTime: 60_000,
  });
  const connected = !!ghMeQ.data;

  // List of repos for the picker. Reuses the existing search endpoint
  // (empty query → most-recently-pushed).
  const reposQ = useQuery({
    queryKey: ["githubRepos", ""],
    queryFn: () => api.searchGithubRepos(""),
    enabled: connected,
    staleTime: 60_000,
  });

  const [owner, name] = repo.split("/");
  const issuesQ = useQuery({
    queryKey: ["githubIssues", owner, name, state, labels],
    queryFn: () => api.listGithubIssues(owner, name, { state, labels }),
    enabled: connected && !!owner && !!name,
    staleTime: 10_000,
  });

  const filtered = useMemo<GitHubIssue[]>(() => {
    const all = issuesQ.data ?? [];
    if (!search.trim()) return all;
    const q = search.toLowerCase();
    return all.filter(
      (i) =>
        i.title.toLowerCase().includes(q) ||
        (i.body || "").toLowerCase().includes(q) ||
        String(i.number).includes(q),
    );
  }, [issuesQ.data, search]);

  // Auto-pick the first repo once the list lands and nothing was previously
  // selected. Done in an effect (not during render) so React doesn't loop.
  useEffect(() => {
    if (connected && !repo && reposQ.data && reposQ.data.length > 0) {
      setRepo(reposQ.data[0].full_name);
    }
    // setRepo is stable across renders (useReducer dispatch wrapper).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected, repo, reposQ.data]);

  return (
    <div className="max-w-5xl mx-auto px-6 sm:px-8 py-12">
      <header className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3">
          <Link
            to="/"
            className="text-xs text-neutral-500 hover:text-neutral-300 transition"
          >
            ← home
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight">github issues</h1>
        </div>
        <p className="text-xs text-neutral-500">
          Browse issues on a connected repo and convert them into pravi tickets.
        </p>
      </header>

      {!connected ? (
        <NotConnected />
      ) : (
        <>
          <Toolbar
            repos={reposQ.data ?? []}
            repo={repo}
            onRepoChange={setRepo}
            state={state}
            onStateChange={setState}
            labels={labels}
            onLabelsChange={setLabels}
            search={search}
            onSearchChange={setSearch}
          />

          {!repo ? (
            <p className="text-sm text-neutral-500 italic mt-8">
              Pick a repo above to see its issues.
            </p>
          ) : issuesQ.isLoading ? (
            <p className="text-sm text-neutral-500 italic mt-8">loading…</p>
          ) : issuesQ.isError ? (
            <div className="mt-6 rounded-2xl border border-rose-400/20 bg-rose-400/[0.06] text-rose-300 px-4 py-3 text-sm">
              {(issuesQ.error as Error).message}
            </div>
          ) : (
            <ul className="mt-6 flex flex-col gap-2">
              {filtered.length === 0 ? (
                <li className="text-sm text-neutral-500 italic">
                  {search ? "no matching issues." : "no issues found."}
                </li>
              ) : (
                filtered.map((i) => (
                  <IssueRow
                    key={i.number}
                    issue={i}
                    onImport={() => setImporting(i)}
                  />
                ))
              )}
            </ul>
          )}

          {importing && owner && name ? (
            <ImportModal
              issue={importing}
              owner={owner}
              repoName={name}
              onClose={() => setImporting(null)}
            />
          ) : null}
        </>
      )}
    </div>
  );
}

function NotConnected() {
  return (
    <div className="mt-8 rounded-2xl border border-amber-400/20 bg-amber-400/[0.04] px-4 py-3 text-sm">
      Connect GitHub from the home page header to browse issues here.
    </div>
  );
}

function Toolbar({
  repos,
  repo,
  onRepoChange,
  state,
  onStateChange,
  labels,
  onLabelsChange,
  search,
  onSearchChange,
}: {
  repos: GitHubRepoResult[];
  repo: string;
  onRepoChange: (r: string) => void;
  state: IssueState;
  onStateChange: (s: IssueState) => void;
  labels: string;
  onLabelsChange: (l: string) => void;
  search: string;
  onSearchChange: (q: string) => void;
}) {
  return (
    <div className="mt-6 flex items-center gap-2 p-1.5 rounded-2xl bg-white/[0.02] border border-white/10 flex-wrap">
      <select
        value={repo}
        onChange={(e) => onRepoChange(e.target.value)}
        className="text-xs px-2.5 py-1.5 rounded-full bg-white/[0.02] border border-white/10 text-neutral-200 focus:outline-none focus:border-blue-400/40 transition max-w-[260px] truncate"
        aria-label="repo"
      >
        <option value="">— pick a repo —</option>
        {repos.map((r) => (
          <option key={r.full_name} value={r.full_name}>
            {r.full_name}
            {r.private ? " (private)" : ""}
          </option>
        ))}
      </select>
      <div className="inline-flex rounded-full border border-white/10 bg-white/[0.02] p-0.5">
        {(["open", "closed", "all"] as IssueState[]).map((s) => {
          const active = s === state;
          return (
            <button
              key={s}
              type="button"
              onClick={() => onStateChange(s)}
              className={`px-3 py-1 text-xs rounded-full transition ${
                active
                  ? "bg-white/10 text-neutral-100"
                  : "text-neutral-500 hover:text-neutral-200"
              }`}
            >
              {s}
            </button>
          );
        })}
      </div>
      <input
        type="text"
        value={labels}
        onChange={(e) => onLabelsChange(e.target.value)}
        placeholder="labels (comma-sep)"
        className="text-xs px-2.5 py-1.5 rounded-full bg-white/[0.02] border border-white/10 text-neutral-200 placeholder-neutral-600 focus:outline-none focus:border-blue-400/40 transition w-40"
        aria-label="filter by labels"
      />
      <div className="flex items-center gap-2 flex-1 min-w-[180px] px-2.5">
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
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder="search title or body…"
          className="flex-1 bg-transparent text-sm text-neutral-100 placeholder-neutral-600 focus:outline-none py-1"
          aria-label="search issues"
        />
        {search ? (
          <button
            type="button"
            onClick={() => onSearchChange("")}
            className="text-neutral-500 hover:text-neutral-200 text-sm leading-none px-1"
            aria-label="clear search"
          >
            ×
          </button>
        ) : null}
      </div>
    </div>
  );
}

function IssueRow({
  issue,
  onImport,
}: {
  issue: GitHubIssue;
  onImport: () => void;
}) {
  return (
    <li className="flex items-start gap-3 rounded-2xl border border-white/10 bg-white/[0.03] hover:bg-white/[0.05] transition px-4 py-3">
      <span
        className={`mt-0.5 inline-flex items-center px-2 py-0.5 rounded-full text-[10px] uppercase tracking-[0.14em] border shrink-0 ${
          issue.state === "open"
            ? "bg-emerald-400/15 text-emerald-200 border-emerald-400/30"
            : "bg-rose-400/15 text-rose-200 border-rose-400/30"
        }`}
      >
        #{issue.number}
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2 flex-wrap">
          {issue.html_url ? (
            <a
              href={issue.html_url}
              target="_blank"
              rel="noreferrer"
              className="text-sm font-medium text-neutral-100 hover:text-blue-300 transition"
            >
              {issue.title}
            </a>
          ) : (
            <span className="text-sm font-medium text-neutral-100">
              {issue.title}
            </span>
          )}
          {issue.labels.map((l) => (
            <span
              key={l.name}
              className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] border border-white/15 bg-white/[0.04] text-neutral-300"
              style={l.color ? { borderColor: `#${l.color}55` } : undefined}
            >
              {l.name}
            </span>
          ))}
        </div>
        {issue.body ? (
          <div className="text-xs text-neutral-500 mt-1 line-clamp-2 whitespace-pre-wrap">
            {issue.body}
          </div>
        ) : null}
        <div className="text-[11px] text-neutral-600 font-mono mt-1.5">
          {issue.user_login ? `@${issue.user_login} · ` : ""}
          {issue.updated_at
            ? `updated ${new Date(issue.updated_at).toLocaleDateString()}`
            : ""}
          {issue.comments > 0 ? ` · ${issue.comments} comments` : ""}
        </div>
      </div>
      <button
        type="button"
        onClick={onImport}
        className="shrink-0 px-3 py-1.5 rounded-full bg-blue-500 hover:bg-blue-400 text-white text-xs font-medium shadow-lg shadow-blue-500/20 transition"
      >
        convert…
      </button>
    </li>
  );
}

function ImportModal({
  issue,
  owner,
  repoName,
  onClose,
}: {
  issue: GitHubIssue;
  owner: string;
  repoName: string;
  onClose: () => void;
}) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [kind, setKind] = useState<TicketKind>("task");
  const [title, setTitle] = useState(issue.title);
  const [body, setBody] = useState(
    `${issue.body || ""}\n\n---\nImported from ${issue.html_url || `#${issue.number}`}.`,
  );
  const [error, setError] = useState<string | null>(null);

  const createMut = useMutation({
    mutationFn: () => {
      const githubIssue: GitHubIssueRef = {
        owner,
        name: repoName,
        number: issue.number,
        html_url: issue.html_url || undefined,
      };
      return api.createTicket({
        kind,
        title: title.trim(),
        body,
        // No parent + no explicit repo path — let the server resolve via the
        // already-cloned repo for this owner/name (or fail loudly if not
        // present). We don't pass `github_repo` here because the user might
        // have multiple checkouts; lazy clone happens at the regular
        // create-ticket flow only.
        github_issue: githubIssue,
      });
    },
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["tickets"] });
      nav(`/tickets/${encodeURIComponent(res.external_id)}`);
    },
    onError: (e: Error) => setError(e.message),
  });

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-xl rounded-2xl border border-white/10 bg-neutral-950 p-6 flex flex-col gap-4"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">
              Convert issue #{issue.number}
            </h2>
            <p className="text-xs text-neutral-500 mt-1">
              {owner}/{repoName}
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

        <div className="flex flex-col gap-3">
          <label className="block">
            <span className="block text-[11px] uppercase tracking-[0.14em] font-semibold text-neutral-400 mb-1.5">
              kind
            </span>
            <div className="inline-flex rounded-full border border-white/10 bg-white/[0.02] p-0.5">
              {(["epic", "feature", "task"] as TicketKind[]).map((k) => {
                const active = k === kind;
                return (
                  <button
                    key={k}
                    type="button"
                    onClick={() => setKind(k)}
                    className={`px-3 py-1 text-xs rounded-full transition ${
                      active
                        ? "bg-white/10 text-neutral-100"
                        : "text-neutral-500 hover:text-neutral-200"
                    }`}
                  >
                    {k}
                  </button>
                );
              })}
            </div>
          </label>

          <label className="block">
            <span className="block text-[11px] uppercase tracking-[0.14em] font-semibold text-neutral-400 mb-1.5">
              title
            </span>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full px-3.5 py-2 rounded-xl bg-white/[0.03] border border-white/10 text-sm text-neutral-100 focus:outline-none focus:border-blue-400/40 transition"
            />
          </label>

          <label className="block">
            <span className="block text-[11px] uppercase tracking-[0.14em] font-semibold text-neutral-400 mb-1.5">
              description (markdown)
            </span>
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={10}
              className="w-full px-3.5 py-2 rounded-xl bg-white/[0.03] border border-white/10 text-sm text-neutral-100 font-mono focus:outline-none focus:border-blue-400/40 transition resize-none"
            />
          </label>

          <p className="text-xs text-neutral-500">
            On submit: posts a comment + "pravi-imported" label back on the
            GitHub issue (best-effort).
          </p>

          {error ? (
            <div className="rounded-xl border border-rose-400/20 bg-rose-400/[0.06] text-rose-300 px-3 py-2 text-sm">
              {error}
            </div>
          ) : null}
        </div>

        <div className="flex items-center gap-3 flex-wrap">
          <button
            type="button"
            onClick={() => createMut.mutate()}
            disabled={!title.trim() || createMut.isPending}
            className="px-4 py-2 rounded-full bg-blue-500 hover:bg-blue-400 text-white text-sm font-medium shadow-lg shadow-blue-500/25 disabled:opacity-40 transition"
          >
            {createMut.isPending ? "creating…" : `create ${kind}`}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-2 rounded-full bg-white/5 hover:bg-white/10 border border-white/10 text-sm transition"
          >
            cancel
          </button>
        </div>
      </div>
    </div>
  );
}

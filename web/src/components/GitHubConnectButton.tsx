import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../lib/api";

/**
 * Header chip: "Connect GitHub" when disconnected, `@login` + avatar when
 * connected. Clicking the connected chip drops a small disconnect menu.
 *
 * The connect path is a full-page redirect to /api/auth/github/login, which
 * 302s to GitHub; the callback redirects back to the home page. This avoids
 * needing a popup or postMessage dance.
 */
export function GitHubConnectButton() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["github", "me"],
    queryFn: () => api.githubMe(),
    refetchOnWindowFocus: true,
  });

  const logoutMut = useMutation({
    mutationFn: () => api.githubLogout(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["github", "me"] });
    },
  });

  if (q.isLoading) {
    return (
      <span className="px-3 py-1.5 rounded-full bg-white/5 border border-white/10 text-xs text-neutral-500">
        github…
      </span>
    );
  }

  const conn = q.data;
  if (!conn) {
    return (
      <button
        onClick={() => {
          window.location.href = api.githubLoginUrl();
        }}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-white/5 hover:bg-white/10 border border-white/10 text-sm font-medium text-neutral-200 transition"
        title="Connect your GitHub account so pravi can push branches and open PRs"
      >
        <GitHubGlyph />
        Connect GitHub
      </button>
    );
  }

  return (
    <details className="relative group">
      <summary className="cursor-pointer list-none inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-white/5 hover:bg-white/10 border border-emerald-400/30 text-sm font-medium text-neutral-200 transition">
        {conn.github_user_avatar_url ? (
          <img
            src={conn.github_user_avatar_url}
            alt=""
            className="size-5 rounded-full"
          />
        ) : (
          <GitHubGlyph />
        )}
        <span>@{conn.github_user_login}</span>
        <span className="text-neutral-500 text-xs">▾</span>
      </summary>
      <div className="absolute right-0 mt-1 min-w-[200px] rounded-xl border border-white/10 bg-neutral-900 shadow-xl shadow-black/40 p-2 z-10">
        <div className="text-[11px] text-neutral-500 px-2 py-1">
          scopes: {conn.scopes || "?"}
        </div>
        <button
          onClick={() => logoutMut.mutate()}
          disabled={logoutMut.isPending}
          className="w-full text-left px-2 py-1.5 rounded-lg text-sm text-rose-300 hover:bg-rose-400/10 transition disabled:opacity-40"
        >
          {logoutMut.isPending ? "disconnecting…" : "disconnect"}
        </button>
      </div>
    </details>
  );
}

function GitHubGlyph() {
  return (
    <svg
      viewBox="0 0 16 16"
      className="size-3.5"
      fill="currentColor"
      aria-hidden
    >
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

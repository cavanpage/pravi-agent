# Connecting Pravi to GitHub

Pravi reaches GitHub through a user-owned OAuth App. Until you finish this
walkthrough, the **Connect GitHub** button in the UI will return a `503
GitHub OAuth is not configured` error and Pravi will refuse to search
repos, create repos, browse issues, or push PRs.

This guide covers the one-time setup. Five minutes, no code.

## What you end up with

Once `PRAVI_GITHUB_OAUTH_CLIENT_ID` and `PRAVI_GITHUB_OAUTH_CLIENT_SECRET`
are set and a user has clicked **Connect GitHub** once:

| Feature | Endpoint / page | What it does |
|---|---|---|
| Repo picker | `GET /api/auth/github/repos/search` | Autocomplete in the new-ticket form — no more typing local clone paths. |
| Repo creation | `POST /api/auth/github/repos/new` | Create a new repo from a starter template, optionally deployed to Cloudflare Pages — see [New repos & Cloudflare Pages](new-repo-and-cloudflare.md). |
| Issue import | `/issues` page | Browse open/closed issues on a connected repo and import them as Pravi tickets in one click. |
| PR creation | `pr_activity` (automatic) | At the end of every task workflow Pravi pushes the dev branch and opens a **PR** against the repo's default branch — ready-for-review by default, draft if `PRAVI_PR_OPEN_AS_DRAFT=true`. |

Routes that gate on the connection live in
[`src/pravi/api/auth_routes.py`](../../src/pravi/api/auth_routes.py); they
return `401 not connected to GitHub` if no active connection exists, and
`503 GitHub OAuth is not configured` if the env vars above are missing.

## 1. Register an OAuth App on GitHub

1. Go to <https://github.com/settings/developers> → **OAuth Apps** →
   **New OAuth App**. (For a shared install, use an org-owned app at
   `https://github.com/organizations/<org>/settings/applications`.)
2. Fill in:
   - **Application name** — anything. `Pravi (local)` is fine.
   - **Homepage URL** — `http://localhost:8765`
   - **Authorization callback URL** —
     `http://localhost:8765/api/auth/github/callback`

   The callback must match exactly. Pravi's default is hard-coded in
   `Settings.github_oauth_redirect_uri` (`src/pravi/config.py`); if you
   change one, change the other.
3. Click **Register application**.
4. On the resulting page, copy the **Client ID** and click
   **Generate a new client secret** — copy that immediately, GitHub only
   shows it once.

You do **not** need to enable "Device Flow" or upload a logo. Leave
"Request user authorization (OAuth) during installation" unchecked unless
you know you want it.

## 2. Scopes

Pravi requests two scopes by default (see
`Settings.github_oauth_scopes` in `src/pravi/config.py`):

- **`repo`** — read + write access to private repos. Required for pushing
  the dev branch and opening PRs against private repos. If you only
  ever target public repos, you can override this to `public_repo` via
  `PRAVI_GITHUB_OAUTH_SCOPES=public_repo,read:user` in your `.env`.
- **`read:user`** — read the authenticated user's profile so the UI can
  show "connected as @you" with avatar.

These are shown to the user on the GitHub consent screen during the
first **Connect GitHub** click.

## 3. Wire the credentials into Pravi

Add the two values to your `.env` (or whichever file `pydantic-settings`
picks up):

```bash
# .env
PRAVI_GITHUB_OAUTH_CLIENT_ID=Iv1.xxxxxxxxxxxxxxxx
PRAVI_GITHUB_OAUTH_CLIENT_SECRET=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Optional overrides (defaults shown):

```bash
PRAVI_GITHUB_OAUTH_REDIRECT_URI=http://localhost:8765/api/auth/github/callback
PRAVI_GITHUB_OAUTH_SCOPES=repo,read:user
PRAVI_GITHUB_OAUTH_SUCCESS_REDIRECT=http://localhost:8765/
```

Restart the web server so the new settings are picked up:

```bash
pravi web
```

If you forget the restart, `/api/auth/github/login` will keep raising
`OAuthNotConfigured` because `get_settings()` is cached.

## 4. Connect a user

1. Open <http://localhost:8765/> and click **Connect GitHub** in the top
   nav. The browser hits `GET /api/auth/github/login`, which 302s to
   GitHub's authorize page.
2. Approve the requested scopes. GitHub redirects back to
   `/api/auth/github/callback?code=…&state=…`. Pravi exchanges the code
   for an access token, stores it on the `github_connections` row, and
   bounces you to `github_oauth_success_redirect` (the home page by
   default).
3. The UI calls `GET /api/auth/github/me` and renders your login + avatar.
   If something went wrong, you land on the home page with
   `?github_auth_error=<reason>` — the most common values:

   | Error | Meaning |
   |---|---|
   | `invalid_state` | The CSRF token didn't match. Click **Connect GitHub** again from the same browser session. |
   | `missing_code_or_state` | GitHub didn't include the expected query params — usually a callback-URL mismatch. |
   | (an exception class name, e.g. `RuntimeError`) | Token exchange failed. Check the client secret. |

## 5. What's now unlocked

With an active connection in `github_connections`, these endpoints stop
returning `401`:

- **`GET /api/auth/github/repos/search?q=<query>`** — backs the repo
  picker in the new-ticket form. Empty `q` returns your most-recently
  pushed repos; non-empty hits GitHub's `/search/repositories` scoped to
  `user:<login>`.
- **`GET /api/auth/github/repos/{owner}/{name}/issues`** — backs the
  `/issues` page. Filters out pull requests; supports `state=open|closed|all`
  and a comma-separated `labels` filter. Each result can be imported as a
  Pravi ticket with one click.
- **`POST /api/auth/github/repos/new`** — create a brand-new repo from a
  starter template, optionally with a Cloudflare Pages deploy. Covered in
  its own guide: [New repos & Cloudflare Pages](new-repo-and-cloudflare.md).
- **`GET /api/auth/github/integrations`** — reports which optional
  integrations are ready (`github.connected`, `cloudflare.configured`);
  the create-repo modal uses it to gate the Pages toggle.
- **Automatic PR creation** — the Temporal `pr_activity` (see
  `src/pravi/activities/pr_activity.py`) reuses the same connection token
  to push the dev branch and open a PR against the repo's default
  branch at the end of every task workflow. PRs open **ready for review**
  by default; set `PRAVI_PR_OPEN_AS_DRAFT=true` to open drafts instead.
  No extra config; if a connection exists, PRs happen.

## 6. Disconnecting

Click **Logout** in the UI, or `POST /api/auth/github/logout`. The row
in `github_connections` is soft-deleted (kept for audit) and subsequent
calls behave as if no user had ever connected. Re-connect any time —
GitHub will skip the consent screen if the scopes haven't changed.

## See also

- [`src/pravi/api/auth_routes.py`](../../src/pravi/api/auth_routes.py) —
  the OAuth endpoints (`/login`, `/callback`, `/me`, `/logout`) plus the
  repo helpers (`/repos/search`, `/repos/{owner}/{name}/issues`,
  `/repos/new`, `/integrations`).
- [`src/pravi/services/github.py`](../../src/pravi/services/github.py) —
  token exchange, connection persistence, and the GitHub REST wrappers
  (`search_user_repos`, `list_repo_issues`, `create_pull_request`,
  `create_repo`, `push_initial_commit`, `ensure_repo_cloned`,
  `comment_on_issue`, `add_labels_to_issue`).
- [`src/pravi/config.py`](../../src/pravi/config.py) — all
  `github_oauth_*` settings and their defaults.
- [New repos & Cloudflare Pages](new-repo-and-cloudflare.md) — the
  create-repo + Pages deploy flow that builds on this connection.

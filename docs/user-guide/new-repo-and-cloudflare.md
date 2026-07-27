# Creating repos & deploying to Cloudflare Pages

Pravi can create a brand-new GitHub repo for you — seeded from a starter
template, optionally auto-deploying to Cloudflare Pages on every push, and
registered as a pravi repo so you can start epics against it immediately.
This guide covers the flow, the Cloudflare connection, and what to do when a
leg of it fails.

Prerequisite: a GitHub connection (see [GitHub OAuth](github-oauth.md)).
Cloudflare is optional — without it you still get the repo + template +
pravi registration, just no Pages deploy.

## The new-repo flow

From the UI, the create-repo modal drives `POST /api/auth/github/repos/new`
(`src/pravi/api/auth_routes.py`). Request fields:

| Field | Default | Meaning |
|---|---|---|
| `name` | — | Repo name on your GitHub account. 409 if taken. |
| `description` | `""` | GitHub repo description. |
| `private` | `true` | Repo visibility. |
| `template` | `vite-react-static` | Starter template slug from `src/pravi/templates/` (`ALL_TEMPLATES`); the modal's picker is fed by `GET /api/auth/github/templates`. See the template table below. |
| `deploy_to_cloudflare_pages` | `false` | Also create a Cloudflare Pages project bound to the repo. |
| `register_in_pravi` | `true` | Clone locally + insert a pravi `Repo` row so tickets can target it right away. |

Steps, in order:

1. **Create the empty repo** on GitHub (`POST /user/repos`).
2. **Push the initial commit** — pravi renders the template files (project
   name substituted) in a temp checkout and pushes to the default branch.
3. **Cloudflare Pages** (optional) — create a Pages project named after the
   repo, git-connected so Cloudflare builds + deploys every push. Build
   settings come from the template's manifest
   (`src/pravi/templates/manifest.py` — `TemplateManifest.build_command` /
   `destination_dir`). Because the initial commit predates the project,
   pravi explicitly triggers the first build after creating it. The site
   comes up at `https://<name>.pages.dev`.
4. **Register in pravi** (optional) — lazy-clone + `Repo` row.

## Templates

| Slug | What you get | Deploys as |
|---|---|---|
| `vite-react-static` | Vite + React + TS + Tailwind starter with a single `frontend` domain in `.builder/domains.yaml`. | Static Pages site (`npm run build` → `dist/`). |
| `llm-chat` | The same stack plus a chat UI and a Pages Function (`functions/api/chat.ts`) calling **Workers AI** through an `AI` binding. Two domains: `frontend` + `api`. | Pages site + Function; the committed `wrangler.toml` (`pages_build_output_dir` + `[ai] binding = "AI"`) provisions the AI binding during the git build — no extra Cloudflare setup, no API keys in the app. |

### About `llm-chat`

- Inference runs on a small Llama model (pinned in `functions/api/chat.ts`,
  one-line swap) under **Workers AI's free tier** — 10,000 neurons/day per
  account, shared across all your apps, resets 00:00 UTC. When the quota is
  exhausted the Function returns 429 and the UI shows a "try again
  tomorrow" banner instead of breaking.
- Verified end-to-end 2026-07-26: a Pages-Edit-scoped token is enough to
  create the project and trigger the build; the git build reads
  `wrangler.toml`, compiles `functions/`, and wires the binding with no
  extra permissions.
- Local dev: `npm run dev` serves the UI only (`/api/chat` 404s);
  `npm run build && npx wrangler pages dev dist` runs the Function against
  your own `wrangler login` session.

The response (`CreateRepoResult`) reports each leg separately —
`initial_commit_pushed`, `pages` / `pages_skipped_reason`, `pravi_repo_id` —
so a partial failure never hides what did succeed. If the commit push fails
you keep the (empty) GitHub repo and can retry manually; Pages and pravi
registration are skipped since they'd point at an empty repo.

The modal is gated by `GET /api/auth/github/integrations`, which reports
`{github: {connected}, cloudflare: {configured}}` — the Pages toggle is
disabled until Cloudflare is connected.

## Connecting Cloudflare

Cloudflare has no self-serve OAuth for third-party apps, so connecting is
paste-a-token (routes in `src/pravi/api/cloudflare_routes.py`, mounted at
`/api/auth/cloudflare/*`):

1. Create an API token at
   <https://dash.cloudflare.com/profile/api-tokens> with
   **Account → Cloudflare Pages → Edit** on the target account.
2. Click **Connect Cloudflare** in the new-repo modal and paste it
   (`POST /connect`). Pravi verifies the token, discovers the accounts it
   can see, auto-picks if there's exactly one (409 + account list if
   several, so the UI can render a picker), and stores the connection in
   the `cloudflare_connections` table.
3. `GET /me` shows the active connection; `POST /disconnect` soft-deletes it.

Alternatively, skip the UI and set `PRAVI_CLOUDFLARE_API_TOKEN` +
`PRAVI_CLOUDFLARE_ACCOUNT_ID` in `.env` — the DB connection takes precedence,
env vars are the fallback (`src/pravi/services/cloudflare.py`).

**One-time browser step that pravi cannot do for you:** authorize
Cloudflare's GitHub App on the account that owns your repos — Cloudflare
dashboard → **Workers & Pages → Create → Pages → Connect to Git**, complete
the GitHub authorization once. Without it, the Pages project may be created
but git-triggered builds silently won't fire.

## Troubleshooting

- **`pages_skipped_reason: "Cloudflare not configured…"`** — connect
  Cloudflare (above) or set the env vars, then create the Pages project by
  hand or recreate the repo.
- **Pages create fails with error `8000012` ("linked to a repository that no
  longer exists")** — your Cloudflare account has an *orphaned* Pages project
  whose GitHub repo was deleted; Cloudflare rejects new git-connected
  projects until it's gone. Cloudflare dashboard → Workers & Pages → the
  stale project → Settings → Delete, then retry.
- **Pages project exists but never deploys** — the GitHub App authorization
  step above is missing, or was revoked. Re-authorize and push any commit.
- **409 on create** — the repo name is taken on your GitHub account. Pages
  project names are also account-unique (they become `*.pages.dev`
  subdomains); pravi pre-checks availability in the modal.

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
| `custom_domain` | `null` | Attach a domain you own (e.g. `app.example.com`) to the **production** deploy. Requires the Pages leg; the zone must be in the same Cloudflare account. See [Custom domains](#custom-domains). |
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
| `vite-react-static` | Vite + React + TS + Tailwind starter. Domains: `frontend` + `e2e`. | Static Pages site (`npm run build` → `dist/`). |
| `llm-chat` | The same stack plus a chat UI and a Pages Function (`functions/api/chat.ts`) calling **Workers AI** through an `AI` binding. Domains: `frontend` + `api` + `e2e`. | Pages site + Function; the committed `wrangler.toml` (`pages_build_output_dir` + `[ai] binding = "AI"`) provisions the AI binding during the git build — no extra Cloudflare setup, no API keys in the app. |

Both templates ship **Playwright end-to-end scaffolding** — a
`playwright.config.ts` reading `E2E_BASE_URL`, an `e2e/smoke.spec.ts`, and a
`preview:` block in `.builder/domains.yaml`. That opts the repo into
per-ticket preview deploys + acceptance-test verification; see
[Acceptance criteria & end-to-end tests](acceptance-criteria-and-e2e.md).

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
   <https://dash.cloudflare.com/profile/api-tokens> with the permissions in
   the table below.
2. Click **Connect Cloudflare** in the new-repo modal and paste it
   (`POST /connect`). Pravi verifies the token, discovers the accounts it
   can see, auto-picks if there's exactly one (409 + account list if
   several, so the UI can render a picker), and stores the connection in
   the `cloudflare_connections` table.
3. `GET /me` shows the active connection; `POST /disconnect` soft-deletes it.

### Token scopes

| Permission | Needed for | Required? |
|---|---|---|
| **Account → Cloudflare Pages → Edit** | creating projects, triggering builds, reading deployments + build logs, registering custom domains | **yes** |
| **Account → Account Settings → Read** | discovering which accounts the token can see (the connect modal's account picker) | yes |
| **Zone → Zone → Read** | finding the zone a custom domain belongs to | only for custom domains |
| **Zone → DNS → Edit** | writing the CNAME that points a custom domain at the site | only for custom domains |

Without the two Zone permissions everything else still works — a custom
domain gets *registered* on the project but stays `pending`, and pravi hands
you the one DNS record to add by hand.

## Custom domains

Pass `custom_domain` at create time (there's a field in the modal under the
Pages toggle) and pravi will:

1. register the hostname on the Pages project;
2. look up its Cloudflare zone;
3. create a proxied `CNAME` → `<project>.pages.dev`;
4. re-read the domain so the result reflects the new DNS.

⚠️ **Custom domains apply to the production deploy only.** Per-ticket
preview deployments always live on `https://<hash>.<project>.pages.dev` —
per-branch custom hostnames need a wildcard-domain setup that isn't a clean
API operation.

The domain must already be a zone in the same Cloudflare account (for an
apex domain, its nameservers must point at Cloudflare). Steps 2–4 are
best-effort: if the token can't read zones or write DNS, the result carries
`dns_skipped_reason` plus a copy-pasteable `manual_dns_record` like

```
app.example.com  CNAME  my-app.pages.dev  (proxied)
```

and the modal renders it with a copy button. The domain goes live as soon as
that record exists. A custom-domain failure never fails repo creation.

## Preview deployments

Projects pravi creates pin `preview_deployment_setting: all`, so **every
non-production branch gets its own deployment**. Two URL shapes:

- **per-commit** — `https://<hash>.<project>.pages.dev`, atomic and
  permanent. This is what pravi's end-to-end verification tests against,
  because it's unambiguously the build of one specific SHA.
- **branch alias** — `https://<sanitized-branch>.<project>.pages.dev`,
  always pointing at that branch's latest build. Display-only for pravi:
  two branches can sanitize to the same label.

Branch names are lowercased with every run of non-alphanumerics collapsed to
a hyphen, so `pravi/t-42-frontend` → `pravi-t-42-frontend.<project>.pages.dev`.

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
- **Custom domain stuck at `pending`** — DNS isn't pointing at the project
  yet. Either add the `manual_dns_record` from the result card, or recreate
  the token with `Zone:Read` + `Zone:DNS:Edit` and re-run.
- **A ticket reports `no preview deployment appeared`** — the project isn't
  git-connected, preview builds are off, or the project name is wrong. See
  the troubleshooting section in
  [Acceptance criteria & end-to-end tests](acceptance-criteria-and-e2e.md).

---
name: cloudflare-deploy
description: >-
  Working on a repo that deploys to Cloudflare Pages (git-connected builds,
  wrangler.toml, Pages Functions, preview deployments). Use when changing
  build config, adding/editing Pages Functions under functions/, editing
  wrangler.toml, or debugging why a deploy/preview didn't build. Biases
  toward fetching current Cloudflare docs over pre-trained knowledge.
---

# Deploying this repo to Cloudflare Pages

This repository deploys via a **git-connected Cloudflare Pages project**:
every push to the production branch triggers a build; every push to any
other branch produces a **preview deployment** at
`https://<commit-or-branch>.<project>.pages.dev`. You do not deploy
manually — you make the repo correct and Cloudflare builds it.

## Ground rules

- **Docs beat memory.** Cloudflare's platform moves fast. Before relying
  on a config key, binding shape, or limit, fetch the current page:
  - Build configuration: https://developers.cloudflare.com/pages/configuration/build-configuration/
  - Committed wrangler config: https://developers.cloudflare.com/pages/functions/wrangler-configuration/
  - Pages Functions routing: https://developers.cloudflare.com/pages/functions/routing/
  - Bindings: https://developers.cloudflare.com/pages/functions/bindings/
- **`wrangler.toml` is the project's source of truth** when it contains
  `pages_build_output_dir`. Git builds read it: build output dir, bindings
  (KV/D1/R2/AI), compatibility date. Prefer editing it over telling the
  user to click through the dashboard.
- **`functions/` is the API.** Files under `functions/` become routes by
  file path (`functions/api/chat.ts` → `POST /api/chat` via
  `onRequestPost`). They are compiled by Cloudflare's build with esbuild —
  keep them dependency-light and, if the app's `tsconfig` excludes the
  directory, type-self-contained.

## Common tasks

- **Change the build**: edit `build_command` / output dir consistently in
  BOTH `wrangler.toml` (`pages_build_output_dir`) and any place the repo
  documents it. Values must agree or builds warn/fail.
- **Add a binding** (KV, D1, R2, AI): declare it in `wrangler.toml`
  (fetch the wrangler-configuration doc above for the exact block), then
  access it via `context.env.<BINDING>` in the Function. Never hardcode
  credentials — bindings exist so no secret ships in the repo.
- **Add an API route**: create `functions/<path>.ts` exporting
  `onRequestGet`/`onRequestPost`/`onRequest`. Return `Response` objects;
  JSON errors with correct status codes (surface upstream 429s as 429).
- **Debug a deploy**: reproduce locally with
  `npm run build && npx wrangler pages dev <output-dir>` (needs a
  logged-in wrangler for real bindings). Build-time failures usually mean
  the build command / output dir / Node version mismatch — check the
  build-configuration doc.

## Verifying your work

The build must pass locally (`npm run build`) before you rely on
Cloudflare's build. If the repo has an e2e suite wired to preview URLs,
that suite is the acceptance gate — don't weaken specs to make a deploy
look green.

"""Vite + React + TypeScript + Tailwind starter — deployable to
Cloudflare Pages with zero config (build command `npm run build`,
output dir `dist`).

The repo lands with one component, one route, and a `.builder/`
config so pravi's decompose / dev / PR flows just work against it.
The README walks the user through both `wrangler` and dashboard-based
Pages deploy paths in case they didn't opt into Pages at create time.
"""

from __future__ import annotations

from pravi.templates.manifest import DeploySpec, TemplateManifest, render_files

_PACKAGE_JSON = """\
{
  "name": "%PROJECT_NAME%",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "e2e": "playwright test",
    "e2e:ui": "playwright test --ui"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@playwright/test": "^1.50.0",
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.3",
    "tailwindcss": "^4.0.0",
    "@tailwindcss/vite": "^4.0.0",
    "typescript": "^5.6.3",
    "vite": "^6.0.0"
  }
}
"""

# Shared by every template — pravi runs this against the deployed preview.
_PLAYWRIGHT_CONFIG = """\
import { defineConfig, devices } from "@playwright/test";

// pravi sets E2E_BASE_URL to the per-commit Cloudflare Pages preview URL
// for the branch under test. Locally it falls back to the dev server.
const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:5173";

export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  // CI=1 selects the JSON reporter, which writes the machine-readable
  // report to STDOUT. pravi parses that stream — do NOT add an
  // `outputFile` here, it would redirect the report to disk and pravi
  // would see an empty result.
  reporter: process.env.CI ? "json" : "list",
  use: {
    baseURL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
"""

_SMOKE_SPEC = """\
import { expect, test } from "@playwright/test";

test("home page loads and renders the app shell", async ({ page }) => {
  const res = await page.goto("/");
  expect(res?.status()).toBeLessThan(400);
  await expect(page.getByTestId("app-root")).toBeVisible();
});
"""

# Appended to every template's .builder/domains.yaml. Deleting the
# `preview:` block is how a repo opts out of the deploy + e2e leg.
_PREVIEW_BLOCK = """\

  - name: e2e
    description: "Playwright end-to-end specs, run against the deployed preview."
    paths:
      - "e2e/**"
      - "playwright.config.ts"
    test: "npx playwright test"
    context_files:
      - "e2e/smoke.spec.ts"
      - "playwright.config.ts"

# pravi deploys each ticket's branch to a Cloudflare Pages preview and runs
# the e2e suite against it, feeding failures back to the dev agent. Delete
# this block to turn that off.
preview:
  provider: cloudflare-pages
  # project: %PROJECT_NAME%   # defaults to the repo's registered Pages project
  wait_timeout_seconds: 900
  e2e:
    dir: e2e
    install: ["npm", "ci"]
    browsers: ["chromium"]
    command: ["npx", "playwright", "test", "--reporter=json"]
    base_url_env: E2E_BASE_URL
    timeout_seconds: 900
"""

_README_E2E = """\

## End-to-end tests

Playwright specs live in `e2e/` and run against a **deployed** URL, not a
local build:

```bash
npm run dev                                     # terminal 1
E2E_BASE_URL=http://localhost:5173 npm run e2e  # terminal 2

# …or against the live site:
E2E_BASE_URL=https://%PROJECT_NAME%.pages.dev npm run e2e
```

When pravi builds a feature for you it pushes the branch, waits for the
Cloudflare Pages **preview** deployment of that exact commit, runs this
suite against it, and — if anything fails — feeds the failures back to the
dev agent to fix. Acceptance criteria you write on a ticket become tests in
this directory.

`playwright.config.ts` selects the JSON reporter when `CI=1`, and pravi
parses that report from stdout. Don't give the JSON reporter an
`outputFile`.
"""

_VITE_CONFIG = """\
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
});
"""

_TSCONFIG = """\
{
  "files": [],
  "references": [
    { "path": "./tsconfig.app.json" }
  ]
}
"""

_TSCONFIG_APP = """\
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src"]
}
"""

_INDEX_HTML = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>%PROJECT_NAME%</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
"""

_MAIN_TSX = """\
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
"""

_APP_TSX = """\
export default function App() {
  return (
    <main
      data-testid="app-root"
      className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-950 to-slate-900 text-slate-100 p-8"
    >
      <div className="max-w-xl text-center space-y-4">
        <div className="text-xs uppercase tracking-[0.18em] text-slate-500">
          %PROJECT_NAME%
        </div>
        <h1 className="text-4xl font-semibold tracking-tight">
          You shipped a thing.
        </h1>
        <p className="text-slate-400">
          This is your blank canvas. Open this repo as an epic in pravi to
          start having an AI agent build features for you.
        </p>
        <div className="pt-4">
          <a
            href="https://github.com/%REPO_FULL_NAME%"
            className="inline-block px-4 py-2 rounded-full bg-slate-800 hover:bg-slate-700 text-sm transition"
          >
            View source on GitHub →
          </a>
        </div>
      </div>
    </main>
  );
}
"""

_INDEX_CSS = """\
@import "tailwindcss";
"""

_GITIGNORE = """\
node_modules/
dist/
.DS_Store
.env
.env.local
*.log
test-results/
playwright-report/
blob-report/
playwright/.cache/
"""

_DOMAINS_YAML = """\
# `.builder/domains.yaml` — pravi's per-domain config for this repo.
#
# A domain is a slice of the codebase the dev agent gets scoped to. For
# a small static site one domain is plenty; split into multiple when
# the codebase grows (e.g. add a `backend` domain when you wire in an
# API).

domains:
  - name: frontend
    description: "React + Tailwind UI."
    paths:
      - "src/**"
      - "index.html"
    test: "npm run build"
    build: "npm run build"
    context_files:
      - "README.md"
      - "src/App.tsx"
"""
_DOMAINS_YAML += _PREVIEW_BLOCK

_README = """\
# %PROJECT_NAME%

A Vite + React + TypeScript + Tailwind starter, scaffolded by
[pravi](https://github.com/cavanpage/pravi-agent) and ready to deploy
to Cloudflare Pages.

## Quick start

```bash
npm install
npm run dev          # http://localhost:5173
npm run build        # build to dist/
```

## Deploy

### Option A — Cloudflare Pages (recommended)

If you ticked **"deploy to Cloudflare Pages"** when creating this repo,
the Pages project is already connected:

- Every push to `main` triggers a Pages build + deploy.
- Your site is live at `https://%PROJECT_NAME%.pages.dev`.

If you didn't, you can connect it now:

1. Cloudflare dashboard → **Workers & Pages** → **Create application**
   → **Pages** → **Connect to Git**.
2. Pick this repo. Cloudflare auto-detects the build settings
   (`npm run build` → `dist/`).
3. Click **Save and Deploy**.

### Option B — anywhere else (Vercel / Netlify / GitHub Pages)

Build output is plain `dist/` — point any static host at it.

## Iterate with pravi

1. Open this repo in pravi (it should already be connected).
2. Create an epic describing what you want to build.
3. Pravi clarifies → decomposes → drafts plans → the dev agent ships
   PRs.
4. Each merged PR triggers a Pages redeploy. Your URL stays the same.
"""
_README += _README_E2E


def render(*, project_name: str, repo_full_name: str) -> TemplateManifest:
    """Manifest for a fresh repo with the given identity. Used by the
    create-repo flow to seed the initial commit + Pages build config."""
    raw = {
        "package.json": _PACKAGE_JSON,
        "vite.config.ts": _VITE_CONFIG,
        "tsconfig.json": _TSCONFIG,
        "tsconfig.app.json": _TSCONFIG_APP,
        "index.html": _INDEX_HTML,
        "src/main.tsx": _MAIN_TSX,
        "src/App.tsx": _APP_TSX,
        "src/index.css": _INDEX_CSS,
        "playwright.config.ts": _PLAYWRIGHT_CONFIG,
        "e2e/smoke.spec.ts": _SMOKE_SPEC,
        ".gitignore": _GITIGNORE,
        ".builder/domains.yaml": _DOMAINS_YAML,
        "README.md": _README,
    }
    return TemplateManifest(
        slug="vite-react-static",
        title="Vite + React + Tailwind",
        description=("TypeScript starter — builds to dist/, deployable anywhere static."),
        build_command="npm run build",
        destination_dir="dist",
        files=render_files(raw, project_name=project_name, repo_full_name=repo_full_name),
        deploy=DeploySpec(pages=True),
    )


__all__ = ["render"]

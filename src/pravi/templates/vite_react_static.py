"""Vite + React + TypeScript + Tailwind starter — deployable to
Cloudflare Pages with zero config (build command `npm run build`,
output dir `dist`).

The repo lands with one component, one route, and a `.builder/`
config so pravi's decompose / dev / PR flows just work against it.
The README walks the user through both `wrangler` and dashboard-based
Pages deploy paths in case they didn't opt into Pages at create time.
"""
from __future__ import annotations

_PACKAGE_JSON = """\
{
  "name": "%PROJECT_NAME%",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
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
    <main className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-950 to-slate-900 text-slate-100 p-8">
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


def _substitute(content: str, *, project_name: str, repo_full_name: str) -> str:
    """Substitute the per-repo placeholders into a template file."""
    return content.replace("%PROJECT_NAME%", project_name).replace(
        "%REPO_FULL_NAME%", repo_full_name
    )


def render(*, project_name: str, repo_full_name: str) -> dict[str, str]:
    """Return the file map (relative path → content) for a fresh
    repo with the given identity. Used by the create-repo flow to seed
    the initial commit."""
    raw = {
        "package.json": _PACKAGE_JSON,
        "vite.config.ts": _VITE_CONFIG,
        "tsconfig.json": _TSCONFIG,
        "tsconfig.app.json": _TSCONFIG_APP,
        "index.html": _INDEX_HTML,
        "src/main.tsx": _MAIN_TSX,
        "src/App.tsx": _APP_TSX,
        "src/index.css": _INDEX_CSS,
        ".gitignore": _GITIGNORE,
        ".builder/domains.yaml": _DOMAINS_YAML,
        "README.md": _README,
    }
    return {
        path: _substitute(
            content, project_name=project_name, repo_full_name=repo_full_name
        )
        for path, content in raw.items()
    }


# `ALL_TEMPLATES` (in the parent module) expects a plain `FILES` dict
# without per-call substitution. We keep `render(...)` as the
# substituting entry point and expose a generic placeholder map under
# `FILES` for any callers that don't need substitution.
FILES: dict[str, str] = render(
    project_name="%PROJECT_NAME%", repo_full_name="%REPO_FULL_NAME%"
)


__all__ = ["FILES", "render"]

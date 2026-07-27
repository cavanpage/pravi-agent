"""AI chat starter — Vite/React UI + a Cloudflare Pages Function calling
Workers AI through an `AI` binding (ADR 0006 slice 1).

Design notes:
  - The committed `wrangler.toml` (`pages_build_output_dir` + `[ai]`)
    is what provisions the binding — git-connected Pages builds read it,
    so pravi's Cloudflare API layer needs no binding support.
  - `functions/api/chat.ts` is auto-detected by the Pages build. It is
    type-self-contained (no `@cloudflare/workers-types` dep) and excluded
    from `tsc -b` (tsconfig.app.json includes only `src/`); Cloudflare's
    esbuild strips the types at deploy time.
  - Workers AI's free tier is 10k neurons/day account-wide. The Function
    maps capacity/rate errors to a 429 the UI renders as a "come back
    tomorrow" banner rather than a broken chat.
  - No API keys anywhere: inference runs via the binding under the
    Cloudflare account that owns the Pages project.
"""

from __future__ import annotations

from pravi.templates.manifest import DeploySpec, TemplateManifest, render_files
from pravi.templates.vite_react_static import (
    _INDEX_CSS,
    _MAIN_TSX,
    _TSCONFIG,
    _TSCONFIG_APP,
    _VITE_CONFIG,
)

# Small + cheap on the free tier. One-line swap; see README for options.
# (llama-3.1-8b was retired from the catalog; 3.2-3b is the current
# small-instruct sweet spot — verified against the model catalog 2026-07.)
_MODEL = "@cf/meta/llama-3.2-3b-instruct"

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

_INDEX_HTML = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>%PROJECT_NAME% — AI chat</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
"""

_APP_TSX = """\
import { useRef, useState } from "react";

type Msg = { role: "user" | "assistant"; content: string };

export default function App() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [quotaHit, setQuotaHit] = useState(false);
  const listRef = useRef<HTMLDivElement | null>(null);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    const next: Msg[] = [...messages, { role: "user", content: text }];
    setMessages(next);
    setInput("");
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ messages: next }),
      });
      const body = (await res.json().catch(() => ({}))) as {
        reply?: string;
        error?: string;
      };
      if (res.status === 429) {
        setQuotaHit(true);
        return;
      }
      if (!res.ok || !body.reply) {
        setError(body.error ?? `request failed (${res.status})`);
        return;
      }
      setMessages((m) => [...m, { role: "assistant", content: body.reply! }]);
      queueMicrotask(() =>
        listRef.current?.scrollTo({ top: listRef.current.scrollHeight }),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "network error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="min-h-screen flex flex-col bg-gradient-to-br from-slate-950 to-slate-900 text-slate-100">
      <header className="px-6 py-4 border-b border-white/10">
        <div className="text-xs uppercase tracking-[0.18em] text-slate-500">
          %PROJECT_NAME%
        </div>
        <h1 className="text-lg font-semibold tracking-tight">
          AI chat · Workers AI
        </h1>
      </header>

      <div ref={listRef} className="flex-1 overflow-y-auto px-6 py-4 space-y-3">
        {messages.length === 0 && (
          <p className="text-slate-500 text-sm">
            Say something — replies come from a small Llama model running on
            Cloudflare's free tier. No API keys involved.
          </p>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            className={
              m.role === "user"
                ? "ml-auto max-w-[80%] rounded-2xl rounded-br-sm bg-blue-500/20 px-4 py-2 text-sm"
                : "mr-auto max-w-[80%] rounded-2xl rounded-bl-sm bg-white/[0.06] px-4 py-2 text-sm whitespace-pre-wrap"
            }
          >
            {m.content}
          </div>
        ))}
        {busy && <div className="text-slate-500 text-sm animate-pulse">thinking…</div>}
      </div>

      {quotaHit && (
        <div className="mx-6 mb-2 rounded-xl border border-amber-400/25 bg-amber-400/[0.06] px-4 py-2 text-sm text-amber-300">
          Daily free-tier limit reached — Workers AI allows 10,000 neurons/day
          per account, resetting at 00:00 UTC. Try again tomorrow.
        </div>
      )}
      {error && (
        <div className="mx-6 mb-2 rounded-xl border border-rose-400/25 bg-rose-400/[0.06] px-4 py-2 text-sm text-rose-300">
          {error}
        </div>
      )}

      <form
        className="flex gap-2 px-6 py-4 border-t border-white/10"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={quotaHit ? "quota resets at 00:00 UTC" : "Ask anything…"}
          disabled={busy || quotaHit}
          className="flex-1 rounded-xl bg-white/[0.04] border border-white/10 px-4 py-2.5 text-sm placeholder-slate-600 focus:outline-none focus:border-blue-400/40 disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={busy || quotaHit || input.trim().length === 0}
          className="rounded-xl bg-blue-500/80 hover:bg-blue-500 px-5 py-2.5 text-sm font-medium disabled:opacity-40 transition"
        >
          Send
        </button>
      </form>
    </main>
  );
}
"""

_CHAT_FUNCTION = """\
// Cloudflare Pages Function: POST /api/chat
//
// Runs a small Llama model through the Workers AI binding declared in
// wrangler.toml ([ai] binding = "AI"). Free tier: 10k neurons/day per
// account — capacity errors are surfaced as HTTP 429 so the UI can show
// a friendly banner instead of breaking.
//
// Types are self-contained on purpose: this file is excluded from the
// app's tsc build and compiled by Cloudflare's esbuild at deploy time.

const MODEL = "%MODEL%";
const MAX_MESSAGES = 20;
const MAX_CONTENT_CHARS = 4000;

const SYSTEM_PROMPT =
  "You are a concise, friendly assistant embedded in a demo chat app. " +
  "Answer briefly and helpfully.";

type ChatMessage = {
  role: "system" | "user" | "assistant";
  content: string;
};

interface Env {
  AI?: {
    run(
      model: string,
      input: { messages: ChatMessage[] },
    ): Promise<{ response?: string }>;
  };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export async function onRequestPost(context: {
  request: Request;
  env: Env;
}): Promise<Response> {
  if (!context.env.AI) {
    return json(500, {
      error:
        "AI binding not configured. Locally: `npm run build && npx wrangler pages dev dist`. " +
        "Deployed: check wrangler.toml's [ai] block.",
    });
  }

  let parsed: { messages?: ChatMessage[] };
  try {
    parsed = (await context.request.json()) as { messages?: ChatMessage[] };
  } catch {
    return json(400, { error: "body must be JSON: { messages: [...] }" });
  }
  const history = (parsed.messages ?? [])
    .filter(
      (m) =>
        (m.role === "user" || m.role === "assistant") &&
        typeof m.content === "string" &&
        m.content.length > 0,
    )
    .slice(-MAX_MESSAGES)
    .map((m) => ({
      role: m.role,
      content: m.content.slice(0, MAX_CONTENT_CHARS),
    }));
  if (history.length === 0) {
    return json(400, { error: "messages is empty" });
  }

  const messages: ChatMessage[] = [
    { role: "system", content: SYSTEM_PROMPT },
    ...history,
  ];

  try {
    const out = await context.env.AI.run(MODEL, { messages });
    const reply = out?.response?.trim();
    if (!reply) {
      return json(502, { error: "model returned an empty response" });
    }
    return json(200, { reply });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    if (/429|rate|capacity|limit|quota/i.test(msg)) {
      return json(429, { error: "Workers AI daily free-tier limit reached" });
    }
    return json(502, { error: `inference failed: ${msg.slice(0, 200)}` });
  }
}
"""

_WRANGLER_TOML = """\
# Pages project config — read by Cloudflare's git-connected builds.
# `pages_build_output_dir` marks this as a Pages project; the [ai]
# block provisions the Workers AI binding the chat Function uses.
name = "%PROJECT_NAME%"
compatibility_date = "2026-07-01"
pages_build_output_dir = "dist"

[ai]
binding = "AI"
"""

_GITIGNORE = """\
node_modules/
dist/
.wrangler/
.DS_Store
.env
.env.local
*.log
"""

_DOMAINS_YAML = """\
# `.builder/domains.yaml` — pravi's per-domain config for this repo.
#
# Two domains: the React UI and the Pages Function API. The Function is
# type-checked by Cloudflare's build at deploy time, not by `tsc` (the
# app tsconfig only includes src/), so both domains share the app build
# as their local check.

domains:
  - name: frontend
    description: "React + Tailwind chat UI."
    paths:
      - "src/**"
      - "index.html"
    test: "npm run build"
    build: "npm run build"
    context_files:
      - "README.md"
      - "src/App.tsx"
  - name: api
    description: "Cloudflare Pages Function calling Workers AI (/api/chat)."
    paths:
      - "functions/**"
      - "wrangler.toml"
    test: "npm run build"
    context_files:
      - "README.md"
      - "functions/api/chat.ts"
"""

_README = """\
# %PROJECT_NAME%

An AI chat app scaffolded by [pravi](https://github.com/cavanpage/pravi-agent):
Vite + React + Tailwind UI, with a Cloudflare Pages Function
(`functions/api/chat.ts`) that runs a small Llama model on
**Workers AI** — free tier, no API keys anywhere.

## Quick start

```bash
npm install
npm run dev                              # UI only — /api/chat will 404
npm run build && npx wrangler pages dev dist   # full app with a real AI binding
```

The second command needs a logged-in `wrangler` (`npx wrangler login`);
it runs the Pages Function locally against your Cloudflare account.

## How the AI works

- `wrangler.toml` declares `[ai] binding = "AI"` — Cloudflare's Pages
  build provisions the binding automatically; inference runs under the
  account that owns the Pages project. No keys ship with the app.
- The model is set in `functions/api/chat.ts`:

  ```ts
  const MODEL = "%MODEL%";
  ```

  Swap it for any Workers AI text model (see the
  [model catalog](https://developers.cloudflare.com/workers-ai/models/)).

## Free tier

Workers AI includes **10,000 neurons/day** per Cloudflare account
(shared across all your apps, resets 00:00 UTC). When the quota is
exhausted the API returns 429 and the UI shows a "try again tomorrow"
banner. Paid usage is $0.011 per 1,000 neurons beyond that.

## Deploy

If you ticked **"deploy to Cloudflare Pages"** when creating this repo,
every push to `main` builds + deploys automatically — the build reads
`wrangler.toml`, compiles `functions/`, and wires the AI binding. Your
app is live at `https://%PROJECT_NAME%.pages.dev`.

To connect manually: Cloudflare dashboard → **Workers & Pages** →
**Create** → **Pages** → **Connect to Git** → pick this repo.

## Iterate with pravi

Open this repo as an epic in pravi — the `frontend` domain covers the
UI, the `api` domain covers the Function. Each merged PR triggers a
Pages redeploy.
"""


def render(*, project_name: str, repo_full_name: str) -> TemplateManifest:
    """Manifest for a fresh AI-chat repo with the given identity."""
    raw = {
        "package.json": _PACKAGE_JSON,
        "vite.config.ts": _VITE_CONFIG,
        "tsconfig.json": _TSCONFIG,
        "tsconfig.app.json": _TSCONFIG_APP,
        "index.html": _INDEX_HTML,
        "src/main.tsx": _MAIN_TSX,
        "src/App.tsx": _APP_TSX,
        "src/index.css": _INDEX_CSS,
        "functions/api/chat.ts": _CHAT_FUNCTION.replace("%MODEL%", _MODEL),
        "wrangler.toml": _WRANGLER_TOML,
        ".gitignore": _GITIGNORE,
        ".builder/domains.yaml": _DOMAINS_YAML,
        "README.md": _README.replace("%MODEL%", _MODEL),
    }
    return TemplateManifest(
        slug="llm-chat",
        title="AI chat (Workers AI)",
        description=(
            "Chat UI + Cloudflare Pages Function calling Workers AI — free tier, no API keys."
        ),
        build_command="npm run build",
        destination_dir="dist",
        files=render_files(raw, project_name=project_name, repo_full_name=repo_full_name),
        deploy=DeploySpec(pages=True, ai_binding="AI"),
    )


__all__ = ["render"]

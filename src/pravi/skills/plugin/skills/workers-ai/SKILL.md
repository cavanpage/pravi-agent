---
name: workers-ai
description: >-
  Working on a repo whose backend calls Cloudflare Workers AI through an AI
  binding (env.AI.run) — model selection, prompt/messages shapes, streaming,
  free-tier quota (neurons) handling. Use when changing the model, the
  inference call, or quota/error handling. Biases toward fetching current
  Cloudflare docs over pre-trained knowledge.
---

# Workers AI in this repo

Inference runs through the **`AI` binding** declared in `wrangler.toml`
(`[ai] binding = "AI"`) — `context.env.AI.run(model, input)` inside a Pages
Function or Worker. No API keys exist anywhere in this codebase; do not
introduce one.

## Ground rules

- **Model slugs rot — verify before changing.** Fetch the current catalog
  before picking or swapping a model:
  https://developers.cloudflare.com/workers-ai/models/
  Text-generation models take `{ messages: [{role, content}, ...] }` and
  return `{ response }` — but confirm the per-model schema on its catalog
  page; shapes differ by family.
- **The free tier is real and account-wide**: 10,000 neurons/day, shared
  across every app on the account, reset 00:00 UTC. Paid usage is
  $0.011 / 1,000 neurons. Pricing/limits doc:
  https://developers.cloudflare.com/workers-ai/platform/pricing/
- **Quota exhaustion is an expected outcome, not a bug.** Surface
  capacity/rate errors from `AI.run` as HTTP 429 with a friendly message;
  the UI should render a "come back tomorrow" state, not a broken chat.
  Never "fix" a 429 by retry-looping — that burns quota faster.

## Common tasks

- **Swap the model**: change the pinned slug constant, verify it exists in
  the catalog, and sanity-check the input/output schema for that model.
  Smaller models (1B–8B instruct) are the free-tier sweet spot.
- **Harden the endpoint**: validate the request body, cap message count
  and content length before calling `AI.run`, keep the system prompt
  server-side, and map thrown errors: capacity/rate → 429 JSON, everything
  else → 502 JSON with a truncated message.
- **Streaming**: many text models support `stream: true` returning an
  event stream — fetch the model's catalog page for the exact shape before
  implementing; return it as `text/event-stream` from the Function.

## Local dev

`npm run dev` serves the UI only — the binding is absent and `/api/*`
should degrade gracefully. `npm run build && npx wrangler pages dev <dist>`
exercises the real binding through the developer's own wrangler login.

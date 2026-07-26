# ADR 0006 — Deploy targets and LLM-app templates

- **Status:** Accepted
- **Date:** 2026-07-26
- **Deciders:** @cavanpage

## Context

Pravi's loop currently ends at "PR opened" (plus a static Cloudflare Pages
deploy for repos created from the starter template). Everything after —
running the product, wiring AI features into it, paying for inference — is
the user's problem. That makes pravi read as a PM/development tool rather
than something that ships working software.

Two capability gaps, often conflated but distinct:

1. **Deploy targets** — where pravi puts the thing it built. Today:
   Cloudflare Pages only, and only for the `vite-react-static` template,
   hardcoded in the create-repo flow (`build_command="npm run build"`,
   `destination_dir="dist"` in `api/auth_routes.py`).
2. **LLM-powered products** — the apps pravi builds can't themselves call
   an LLM. There is no template with an AI feature, no story for which
   inference provider a generated app uses, and no way for the dev agent
   to operate the deployment it just shipped.

Constraints that shaped the decision:

- **Cost floor matters.** The demo target is "describe an app → it's live
  with working AI features, for ~$0". Cloudflare Workers AI has a real
  free tier (10,000 neurons/day, resets daily) and serves open-weight
  models (Llama, Qwen, Gemma, Mistral). AWS Bedrock has no free inference
  tier; its floor is Amazon Nova Micro at ~$0.035/1M input + $0.14/1M
  output tokens — very cheap, never free. (Prices as of 2026-07; verify
  before building the Bedrock slice.)
- **Integration cost is asymmetric.** Pravi already holds a Cloudflare
  token + account (`cloudflare_connections`, `services/cloudflare.py`).
  A Workers AI *binding* means the deployed app needs no API key at all.
  Bedrock requires a new AWS credential subsystem (keys/IAM, region
  model-access enablement) — a second `CloudflareConnection`-sized effort
  before the first token flows.
- **Pravi's own agents are out of scope.** ADR 0002 (as amended) fixed
  pravi's architect + dev agents on Claude. This ADR is about the
  inference used by the *products pravi builds*, which is free to be
  open-weight or non-Anthropic.

## Decision

**Extend pravi from "opens PRs" to "ships running, optionally AI-powered
products", in three phased slices — Cloudflare-first, Bedrock second,
self-hosting explicitly deferred.**

### Slice 1 — `llm-chat` template on Cloudflare Workers AI (free tier)

A second entry in `templates/ALL_TEMPLATES`: a Vite/React chat (or
single-AI-feature) UI backed by a Pages Function / Worker that calls
Workers AI through a binding (small Llama or Qwen model by default).

Prerequisite refactor: **templates grow a manifest.** Today a template is
a dict of files; the create-repo flow hardcodes the build config. The
manifest carries what the deploy needs:

```python
@dataclass
class TemplateManifest:
    slug: str                    # "vite-react-static", "llm-chat"
    files: dict[str, str]        # rendered file tree
    build_command: str
    destination_dir: str
    deploy: DeploySpec | None    # target kind + required bindings
```

The create-repo endpoint maps `template → manifest` instead of assuming
Vite/dist, and the Cloudflare service learns to provision the Workers AI
binding alongside the Pages project.

### Slice 2 — Bedrock as the second inference provider

The same template family, provider-switched to Bedrock (Nova Micro or
Llama-on-Bedrock) for the "cloud-vendor hosted frontier API" story. This
slice introduces the AWS credential connect flow (an `aws_connections`
table mirroring `cloudflare_connections`) and whatever deploy target the
template needs on AWS. Deliberately second: the template/deploy
abstractions get designed against two real providers, not extrapolated
from one.

### Slice 3 — self-hosted open weights: deferred

Not built now. Self-hosting Llama/Qwen/Gemma means GPU provisioning,
scaling, and cold-start management — it destroys the cost story at small
scale, and slices 1–2 already serve the same open-weight models
serverlessly. See "When to revisit".

### MCP loadouts for the dev agent, not hand-written wrappers

For agent-side deploy/debug capability (tail logs, redeploy, inspect a
failed build), attach vendor MCP servers (Cloudflare's, AWS's) to the dev
agent per stack rather than wrapping each vendor API by hand.
claude-agent-sdk already speaks MCP; the stack catalog's
`additional_skills` concept (ADR 0004) is the natural place to declare a
stack's MCP loadout (e.g. a `cloudflare-workers-ai` stack carries the
Cloudflare MCP server).

### No `DeployTarget` Protocol yet

Same discipline as the sandbox seam (ADR 0003): extract the Protocol when
the second target actually lands (slice 2), not before. Until then the
Cloudflare service is the one concrete implementation.

## Consequences

### Wins

- Completes the loop: idea → clarified plan → built → **live at a URL**,
  with AI features, at $0 for the default path. Much stronger demo than
  PM/tracking tooling alone.
- Slice 1 rides entirely on existing seams — templates package, create-repo
  flow, Cloudflare connection — no new vendor, no new credential system.
- Binding-based inference means generated apps ship without secrets.
- Open-weight models (Llama/Qwen/Gemma) arrive without pravi operating
  any GPU.
- Deployed-app inference spend is a future extension of the existing
  per-persona/per-stack FinOps views ("what does this product cost to run",
  alongside "what did it cost to build").

### Costs (acknowledged)

- **Surface-area growth in a crowded market.** Deploy tooling is a
  commodity. The differentiator to protect is the end-to-end agentic loop,
  not platform breadth — templates keep it opinionated; this ADR is not a
  mandate to become a general deploy platform.
- The template manifest refactor touches the create-repo flow and its
  partial-success semantics.
- Slice 2's AWS credential subsystem is real work with real security
  surface (storing cloud keys), and Bedrock onboarding friction (model
  access per region) lands on the user.
- Workers AI free tier is account-wide and shared across models — a
  chatty deployed app can exhaust 10k neurons/day; the template should
  degrade gracefully (surface the 429, don't silently break).

## Alternatives considered

### Bedrock first

Rejected for sequencing, not merit: no free tier, and the credential
subsystem must exist before the first demo works. Cloudflare-first gets a
live AI app with zero new plumbing; Bedrock lands second where its
"frontier API" strength (Claude/Nova access) actually differentiates.

### Self-hosted inference now (Llama/Qwen/Gemma on GPUs)

Rejected: operational lift (provisioning, scaling, cold starts) is out of
all proportion to the POC's needs, and per-token serverless beats a
dedicated GPU on cost until sustained volume is high.

### General-purpose deploy platform (arbitrary targets, IaC)

Rejected: that's Terraform/SST/Vercel's business. Pravi deploys what its
templates describe, nothing more.

### Hand-written vendor API wrappers for agent deploy/debug

Rejected in favor of MCP loadouts — wrappers rot, and MCP servers are
maintained by the vendors themselves.

## When to revisit

**Build slice 3 (self-hosting) if:**
- A user has a privacy/data-residency requirement serverless providers
  can't meet.
- Sustained inference volume on a deployed product crosses the line where
  a dedicated GPU beats per-token pricing.

**Extract the `DeployTarget` Protocol early if:**
- Slice 1 review shows the Cloudflare service accumulating target-specific
  branching before AWS lands.

**Reconsider the whole direction if:**
- Deploy/AI-template features measurably slow the core loop (plan →
  build → PR) — the loop is the product; these slices serve it.

**Re-check pricing before each slice:**
- The free-tier and Nova Micro numbers above are 2026-07 snapshots;
  vendor pricing moves.

## Related

- ADR [0002 — LLM-agnostic architect, Claude-only dev](0002-llm-agnostic-architect-claude-only-dev.md)
  (as amended) — pravi's *own* agents stay on Claude; this ADR governs
  inference for *generated products* only.
- ADR [0003 — Sandbox seam](0003-sandbox-seam-no-local-mounts.md) — the
  "second implementation extracts the seam" discipline reused here.
- ADR [0004 — Agent personas](0004-agent-personas.md) — stacks'
  `additional_skills` as the mount point for MCP loadouts.
- `src/pravi/templates/` — template registry the manifest refactor extends.
- `src/pravi/services/cloudflare.py`, `src/pravi/api/auth_routes.py` —
  the create-repo + Pages flow slice 1 builds on.
- [New repos & Cloudflare Pages guide](../user-guide/new-repo-and-cloudflare.md).

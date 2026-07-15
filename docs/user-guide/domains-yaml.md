# `.builder/domains.yaml` — the domain manifest

Pravi refuses to run against a repo that doesn't declare its domains. The
manifest lives at **`.builder/domains.yaml`** in the *target* repo (the repo
pravi builds features in, not pravi itself) and is the contract that scopes
every agent run: the architect reads it to understand the codebase's shape,
and each dev agent is pinned to exactly one domain from it.

You can also pass a manifest that lives elsewhere with `--domains-file`
(every `pravi ticket …` / `pravi dev` / `pravi plan` command accepts it) —
useful before the target repo has merged its manifest.

## Schema

```yaml
domains:
  - name: shared                     # required — slug: [a-zA-Z0-9_-]+
    description: "Cross-cutting utilities used by cli and dashboard."
    paths:                           # required, at least one entry
      - "packages/shared/**"
    test: "npm test -w packages/shared"
    build: "npm run build -w packages/shared"
    context_files:
      - "packages/shared/README.md"
      - "packages/shared/src/index.ts"
```

| Field | Required | What it does |
|---|---|---|
| `name` | yes | Slug identifying the domain (`shared`, `cli`, `frontend`, …). Must be unique within the file; alphanumerics plus `-`/`_` only. |
| `paths` | yes (≥ 1) | Glob patterns for the slice of the repo this domain owns. Injected into the dev agent's system prompt as the boundary it must stay inside. |
| `description` | no | One-liner shown to the agents ("what is this domain for"). |
| `test` | no | Shell command that proves the domain still works. The dev agent runs it before declaring a task done; `pravi ticket run` uses it as the default smoke command. |
| `build` | no | Shell command to build the domain, when that's distinct from `test`. |
| `context_files` | no | Repo-relative files whose *contents* are pre-packed into the architect's context (and listed for the dev agent). Pick the few files that explain the domain — a README, the main entry point, a schema. |

Validation happens at load time (`src/pravi/domains/registry.py`,
pydantic-backed): a missing file, an empty `domains:` list, a non-slug name,
duplicate names, or an empty `paths` list all fail fast with a clear error.

## How each field is used

- **Pinning.** Every ticket carries a `domain` name. At run time pravi looks
  the domain up in the manifest and builds the dev agent's system prompt from
  it: the domain's `description` and `paths` become explicit instructions to
  stay within those globs (`src/pravi/prompts/developer.py`). Scoping is
  prompt-level today — there is no filesystem jail — which is also why
  worktrees + human PR review remain the backstop.
- **Architect context.** Rather than RAG, pravi pre-packs *deterministic*
  context (see [ADR 0005](../adr/0005-no-rag-tool-use-and-explicit-context.md)):
  the contents of `context_files` plus a directory tree of the domain's
  `paths`. Keep `context_files` short and high-signal — it's opt-in context,
  not a search index.
- **Tests.** `test` is the domain's definition of green. If you leave it out,
  the dev agent has no domain-level check to run and will rely on whatever
  the plan specifies.

## Choosing domains

One domain is fine for a small repo (the starter template pravi scaffolds
for new repos ships with a single `frontend` domain). Split when different
slices have different owners, test commands, or conventions — e.g. `cli`,
`dashboard`, `shared`, `docs`. Domains may overlap in principle, but
non-overlapping `paths` keep parallel dev runs from stepping on each other.

A fuller example manifest lives at
[`examples/blissful-infra-domains.yaml`](../../examples/blissful-infra-domains.yaml).

## Inspecting

```bash
uv run pravi ticket list-domains --repo /path/to/repo
# or with an out-of-tree manifest:
uv run pravi ticket list-domains --repo /path/to/repo --domains-file ./domains.yaml
```

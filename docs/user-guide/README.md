# Pravi User Guide

Bringing pravi to your own repo takes four pieces of setup. Each guide below
covers one of them — read them in order if you're starting from scratch, or
jump straight to the one you need.

| # | Guide | What it covers |
|---|-------|----------------|
| 1 | [`.builder/domains.yaml`](domains-yaml.md) | The domain manifest pravi reads from your repo — schema, path scoping, allowed tools, and how a dev agent gets pinned to one domain. |
| 2 | [GitHub OAuth registration](github-oauth.md) | Registering the OAuth App that unlocks repo search, issue import, and auto-PR — including the callback URL and the env vars to set. |
| 3 | [Persona & stack catalog](personas-and-stacks.md) | The built-in personas and stacks the architect picks from during decomposition, and how to read (or extend) the catalog. |
| 4 | [Budget ceilings & spend views](budgets.md) | How `$` ceilings inherit Epic → Feature → Task, where they're enforced, and the per-persona / per-stack spend breakdowns in the UI. |

For architecture decisions ("why Temporal?", "why no RAG?") see
[`../adr/`](../adr/README.md). For a project overview and quickstart, see the
[top-level README](../../README.md).

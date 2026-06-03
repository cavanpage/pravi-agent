# ADR 0003 — Sandbox seam, no local mounts

- **Status:** Accepted
- **Date:** 2026-05-26
- **Deciders:** @cavanpage
- **Supersedes (in part):** The implicit "Repo.local_path is the source of
  truth" assumption from earlier slices.

## Context

The dev agent runs filesystem-mutating tools (`Edit`, `Write`, `Bash`) inside
a per-ticket `git worktree`. Today the working directory is a path on the
user's machine: a clone in `~/.pravi/repos/owner__name` plus a worktree in
`~/.pravi/worktrees/<ticket-id>`. The user picks the repo through three
overlapping inputs on `/new`: a GitHub search, a dropdown of local Repo rows,
and a free-form local path field.

Two related problems:

1. **UX**: three overlapping pickers for one decision — confusing.
2. **Architecture**: pravi assumes there's always a host filesystem the agent
   can write to. That assumption blocks the obvious next step (running pravi
   hosted, with each dev run in an ephemeral remote sandbox — Docker
   container / Cloudflare Sandbox SDK / e2b / etc).

We want to keep running locally for the foreseeable future but design the
seam now so swapping in a remote sandbox backend is a config flip later, not
a rewrite.

## Decision

**Introduce a `Sandbox` Protocol that owns the working directory's
lifecycle. Treat the local-worktree flow as one impl behind that Protocol.
Drop the "local mount" concept from the UI; repos are identified by their
GitHub coordinates.**

- New `src/pravi/agents/sandbox/` module:
  - `protocols.py` — `Sandbox` Protocol + `SandboxProvisionRequest` /
    `SandboxHandle` dataclasses (opaque, JSON-serializable so Temporal can
    pass them between activities).
  - `local.py` — `LocalWorktreeSandbox`. Lazy-clones into
    `clone_base/<owner>__<name>` (idempotent) on first provision, then
    `git worktree add` per ticket. Push + commit-counting are local
    `git` subprocesses against the worktree.
  - `factory.py` — `get_sandbox()` reads `PRAVI_SANDBOX_BACKEND` (default
    `"local"`).
- `activities/sandbox_activity.py` — Temporal-facing wrappers
  (`provision_sandbox`, `cleanup_sandbox`). They look up `get_sandbox()`
  and dispatch. Workflows call these instead of the old
  `create_worktree`/`remove_worktree`.
- `activities/pr_activity.py` — receives a `SandboxHandle` and asks the
  sandbox for `commits_ahead` + `push_branch`, instead of running git
  subprocesses directly. Lets a remote sandbox push from inside itself
  without the workflow caring.
- `Repo` row: `local_path` becomes nullable. New rows always have
  `github_owner` + `github_name`; the local-clone path is derived by the
  sandbox impl, not stored as identity.
- `/new` picker: GitHub-only. No local-path dropdown, no manual path field,
  no `/api/repos` endpoint, no `PRAVI_TARGET_REPOS` setting. Cleaner one-
  choice UX.

## Consequences

### Wins
- One concept on the picker: "which GitHub repo?" The local clone is an
  implementation detail of the local backend, invisible to the user.
- The dev + pr activities consume a `SandboxHandle`, not a host filesystem
  path. Swapping to a remote backend later is "implement the Protocol +
  flip config" — no caller changes.
- Repos are GitHub-identified, which matches the issues page, the PR push,
  the back-link comments, and the future remote-sandbox direction. One
  identity model end-to-end.
- Dead code drops: `PRAVI_TARGET_REPOS` env var, the comma-separated parser
  validator, the `/api/repos` endpoint, the local-path picker UI.

### Costs (acknowledged)
- "Point pravi at my existing checkout" stops working for new tickets —
  intentional, but a real loss of ergonomics for the local-dev case where
  the user is already iterating in their own repo. We're betting the
  hosted-eventually goal is worth more than that.
- Pre-existing tickets with `local_path`-only Repo rows still work (the
  sandbox falls back to `local_path` when no GitHub coordinates exist),
  but only as legacy — new tickets always have GitHub identity.
- The Protocol abstraction has *one* impl today. Speculative architecture
  has a known failure mode (interface rots before second impl appears).
  Accepted because the seam shapes are simple (provision / push / cleanup)
  and the future remote impls have known requirements.

## Alternatives considered

### Just clean up the UI, keep the local-mount model
Considered. Rejected because the underlying confusion — "is this repo
something pravi knows about, or just a path?" — would persist, and we'd be
fighting the same fight when we add Docker/remote backends.

### Sandbox abstraction, but keep `local_path` as identity
Rejected. If the sandbox owns the working dir, the canonical identity should
match the sandbox-agnostic concept: GitHub coordinates. A "repo's path"
makes no sense for a Cloudflare Sandbox.

### Implement Docker sandbox today
Tempting. Rejected because we'd be writing a second impl before we know what
the first impl's actual contract pain points are. Local now, second impl
when the seam has been pressure-tested by real usage.

## When to revisit

**Add a remote sandbox impl when:**
- Pravi moves to hosted (multi-tenant or hosted-single-tenant).
- Local agents need stronger isolation (running tasks against repos with
  untrusted dependencies, untrusted plans, etc.).
- We want reproducibility-by-default — every dev run from a clean state,
  no host environment drift.

Concrete sketch: `agents/sandbox/docker.py` (`DockerSandbox`) or
`agents/sandbox/cloudflare.py` (`CloudflareSandbox` against the Sandbox
SDK) implements the same Protocol. `PRAVI_SANDBOX_BACKEND=docker` flips
the factory. The dev_activity may need to move execution *inside* the
sandbox (the Claude SDK can't `cd` into a remote container), but the
Temporal activity boundary stays where it is.

**Reconsider this whole thing if:** the seam adds friction for the local
case without anyone using a remote backend after, say, 6 months. Delete
the Protocol and inline `LocalWorktreeSandbox` back into the activities.

## Related

- `src/pravi/agents/sandbox/` — the new module.
- `src/pravi/activities/{sandbox,dev,pr}_activity.py` — Temporal-facing wrappers.
- [ADR 0001](0001-orchestration-temporal-no-langgraph.md) — Temporal owns
  the lifecycle around the sandbox; this ADR clarifies what's inside it.
- [ADR 0002](0002-llm-agnostic-architect-claude-only-dev.md) — the dev
  agent (the thing that runs inside the sandbox) is Claude-only by design.

# Personas and stacks

Every ticket in Pravi has two optional fields that shape how the dev
agent runs it: **persona** (what kind of work it is) and **stack**
(what tech it's in). The architect proposes both during decomposition,
and you can override either one in the UI before approving the tree.

This page covers:

- What's in the persona catalog (and what each persona changes about
  the dev agent run).
- The open-set stack model — what ships out of the box and what
  happens for stacks we haven't named.
- How the architect picks `persona` + `stack` while decomposing your
  epic.
- Where to override the assignment before approve.

The design rationale (the *why* — two axes vs. one combined field,
why "Claude Skills" as the loadout primitive, etc.) lives in
[ADR 0004 — Agent personas](../adr/0004-agent-personas.md). This page is
the operator's reference.

---

## Persona catalog

The catalog is one file: [`src/pravi/personas/catalog.py`](../../src/pravi/personas/catalog.py).
It has 19 entries total — **6 active**, **13 coming-soon**.

| Persona | Group | Status | What it's for |
|---|---|---|---|
| `architect` | architecture | active | Container-only design; output is the plan, not committed code |
| `frontend` | engineering | active | UI implementation, state, accessibility |
| `backend` | engineering | active | Business logic, APIs, schema discipline, test-first |
| `tester` | quality | active | Tests only — hard rule against editing source outside `tests/` |
| `tech_writer` | product | active | Markdown docs, READMEs, ADRs, release notes |
| `other` | other | active | Escape hatch — generic dev prompt, no persona framing |
| `product_manager`, `ux_designer`, `dba`, `data_engineer`, `ml_engineer`, `ai_fde`, `perf_tester`, `chaos_engineer`, `pen_tester`, `devops`, `sre`, `appsec`, `finops` | various | **coming_soon** | Listed in the picker as disabled, blocked from architect assignment, fall back to the generic prompt if forced |

Only the active set is offered to the architect during decomposition.
Coming-soon entries are visible in the UI as a roadmap signal, but the
picker disables them and the decompose prompt's persona list is filtered
to active-only.

### What a persona actually changes

A `Persona` is a small dataclass:

```python
@dataclass(frozen=True)
class Persona:
    slug: str
    name: str
    group: PersonaGroup
    status: PersonaStatus
    description: str
    system_prompt_modifier: str = ""   # paragraph appended to dev prompt
    baseline_skills: list[str] = []    # Claude Skills hint, persona-level
```

Two things vary per persona:

1. **`system_prompt_modifier`** — a paragraph appended to the bottom of
   the dev agent's system prompt. It's where role framing lives — for
   example, `tester`'s modifier contains the hard rule:

   > **You must not change source code outside `tests/`** (or its
   > language-specific equivalent — `__tests__`, `*_test.go`, etc).

   …and `architect`'s modifier forbids file writes entirely. The
   modifier goes *after* the generic dev prompt so it can override or
   tighten the defaults above it.

2. **`baseline_skills`** — a list of Claude Skill names recommended for
   this persona regardless of stack. Stack adds more (see below) and
   the dev prompt surfaces the **union** as a hint.

   In v1 this is *advisory*: the prompt nudges the agent toward the
   skill conventions, the SDK doesn't load them programmatically. The
   catalog records the recommendation today so the wiring is a
   one-place change once `claude-agent-sdk` grows a clean
   skill-loading API.

### What a persona does **not** change (yet)

ADR 0004 anticipates two more axes — they're not in the catalog code
today, but worth being explicit about so you don't go looking:

- **Model defaults.** The ADR's pitch is "tech_writer routes to
  Haiku, architect routes to Opus." The seam exists (each architect
  mode already pins its own model — see ADR 0002), and `tech_writer`'s
  description still mentions "defaults to a cheaper model," but
  there's no `Persona.model` field yet. Until there is, the dev agent
  uses whatever model is configured globally or per-request.
- **Tool allowlist.** The persona doesn't gate which Bash/Edit/etc.
  tools the SDK exposes. `tester`'s "no source outside `tests/`" is
  enforced *by the prompt*, not by withholding `Edit`. The scope
  guardrail that *is* enforced — domain path globs — comes from
  `.builder/domains.yaml`, not the persona.

If you're adding persona-keyed routing or tool gates, the catalog is
the right place to extend; the prompt assembly in
[`src/pravi/prompts/developer.py`](../../src/pravi/prompts/developer.py)
is where the new fields would land.

### Where the modifier lands in the prompt

Concretely, this is the assembled prompt the dev agent sees (see
`system_prompt()` in `src/pravi/prompts/developer.py`):

```
You are a developer agent for the `<domain>` domain of `<repo>`.

Domain description:
<domain.description>

You are working inside an isolated git worktree at:
  <cwd>

Scope rules (important):
  - You may freely read any file in the worktree for context.
  - You may only WRITE to files matching these patterns:
      <domain.paths>
  - Stay inside the worktree. Do not modify files elsewhere on disk.

Workflow:
  - Read the task. If you need more context, read the relevant files first.
  - ... (generic guidance)

Style:
  - ... (generic guidance)

Persona — <persona.name>:
<persona.system_prompt_modifier>

Recommended Claude Skills for <persona.name> on the <stack.name> stack:
`skill_a`, `skill_b`, `skill_c`. Lean on the conventions those skills
carry; if a skill isn't available, fall back to the project's existing
conventions.
```

The bottom two blocks only appear when a persona is assigned and at
least one of (modifier, skills) is non-empty. Persona `other` and the
coming-soon personas have an empty modifier and produce no extra
block — the dev agent runs with the bare domain framing.

---

## Stacks (open-set)

Where personas are a closed catalog, **stacks are open** — the
architect is allowed to mint a slug that doesn't exist yet. The
starter catalog in [`src/pravi/personas/stacks.py`](../../src/pravi/personas/stacks.py)
just gives common combos a known name; everything else resolves to
`unknown`.

| Slug | Name | Skills added on top of persona | Detect hint |
|---|---|---|---|
| `python-fastapi` | Python · FastAPI | `python`, `fastapi`, `pytest` | `pyproject.toml` with `fastapi` in deps |
| `python-django` | Python · Django | `python`, `django`, `pytest` | `manage.py` + `django` in deps |
| `python-stdlib` | Python (no web framework) | `python`, `pytest` | `pyproject.toml` without a web framework |
| `typescript-react` | TypeScript · React | `typescript`, `react` | `package.json` with `react` in deps |
| `typescript-vue` | TypeScript · Vue | `typescript`, `vue` | `package.json` with `vue` in deps |
| `typescript-node` | TypeScript · Node (no UI framework) | `typescript`, `node` | `package.json`, no UI framework |
| `java-spring` | Java · Spring Boot | `java`, `spring-boot`, `junit` | `pom.xml` or `build.gradle` with `spring` |
| `go-stdlib` | Go | `go`, `go-test` | `go.mod` |
| `rust` | Rust | `rust`, `cargo-test` | `Cargo.toml` |
| `markdown` | Markdown / docs | _(none)_ | persona is `tech_writer` |
| `unknown` | Unknown / generic | _(none)_ | fallthrough |

The `detect_hints` are documentation only in v1 — they describe what a
detector *would* look for. No auto-detection runs today; the architect
infers stack from the same context, and you can override.

### What "open-set" means in practice

If the architect picks `kotlin-ktor` for a Kotlin Ktor feature, that
slug is allowed even though it isn't in the table above. It just
resolves to the `unknown` stack at lookup time (`get_stack()` returns
`DEFAULT_STACK` for any unknown slug), so:

- The dev agent's system prompt won't have a skills hint for it.
- Per-stack spend aggregation will bucket those runs under their
  literal slug (the value stored on the ticket).
- Nothing crashes.

If you find yourself wanting a slug to carry real skills, add a `Stack`
entry to `stacks.py` and include it in `KNOWN_STACKS`. That's the only
change needed — the prompt assembly and parser pick it up
automatically.

### How stack composes with persona

The dev prompt's skills hint is **`persona.baseline_skills ∪ stack.additional_skills`**,
deduplicated, preserving order. Example for a `(backend, python-fastapi)` task:

```
baseline_skills (backend) = []
additional_skills (python-fastapi) = ["python", "fastapi", "pytest"]
→ surfaced in prompt: `python`, `fastapi`, `pytest`
```

For `(tech_writer, markdown)` the union is empty and no skills line is
rendered — markdown work doesn't need a loadout hint.

---

## How the architect assigns persona + stack

When you ask Pravi to decompose an epic, the architect runs with
[`src/pravi/prompts/decompose.py`](../../src/pravi/prompts/decompose.py).
That prompt lists every **active** persona and every **known** stack
slug, and includes this guidance:

> Assign a persona when the work is genuinely role-shaped (test-only
> changes → `tester`; doc-only → `tech_writer`). For mixed work, leave
> persona unset. Stack is whatever tech the work is in. Tasks under a
> Python FastAPI feature default to `python-fastapi`; flip them
> individually if a task is in a different stack.

The architect's YAML output looks like this:

```yaml
features:
  - title: "Wire up the /tickets endpoint"
    description: "Add the FastAPI route + handler + happy-path test."
    domain: "backend"
    persona: "backend"            # optional
    stack: "python-fastapi"       # optional
    depends_on: []
    tasks:
      - title: "Write the route + handler"
        description: "..."
        persona: "backend"        # optional — overrides feature persona
        stack: "python-fastapi"   # optional — overrides feature stack
      - title: "Write the integration test"
        description: "..."
        persona: "tester"         # narrower than the feature
```

Three rules to keep in mind:

1. **Persona is optional.** Omit the field entirely if the work isn't
   genuinely role-shaped — the dev agent runs with the generic prompt
   (equivalent to `persona: other`).
2. **Tasks inherit from their feature.** A task without `persona` /
   `stack` picks up whatever the feature declared. If the feature also
   omitted them, the task is unassigned.
3. **Stack slugs are not validated against `KNOWN_STACKS`.** The
   parser accepts whatever string the architect emitted; unknown slugs
   simply degrade to no skills hint. This is intentional —
   `kotlin-ktor` is a legitimate value even if we don't have a Stack
   entry for it.

### What if the architect picks a coming-soon persona?

The decompose prompt only lists active personas, so the architect
shouldn't pick one. If somehow a coming-soon slug ends up on a ticket
(manual override, hand-edited YAML), the dev agent resolves it
normally but the persona's `system_prompt_modifier` is empty —
effectively falling back to the generic prompt, with a soft warning
logged at the call site.

---

## Overriding before approve

The decompose flow is **propose → review → approve**. Nothing is
written to GitHub or kicked off until you click approve, which means
every persona / stack assignment is up for edit in between.

There are two override paths:

1. **Edit the YAML directly.** The decompose panel shows the
   architect's raw YAML in an editable code area. Change `persona:` or
   `stack:` on any feature or task, save, and re-run "Parse" — the
   tree re-materializes from your edit. This is the right move when
   you want to re-shape multiple tickets at once or assign a slug the
   architect didn't think to mint (e.g. flipping a feature to a custom
   `kotlin-ktor` stack).
2. **Pick at manual creation.** The new-ticket form has a
   **PersonaPicker** dropdown (active personas selectable, coming-soon
   disabled with a tooltip) and a stack field.

Persona and stack are **set once, at ticket creation** — there is
currently no endpoint to edit them on an existing ticket (the only
ticket-level PATCH is `/api/tickets/{external_id}/budget`). Editing
persona/stack in place on the ticket page is on the roadmap; until
then, get the assignment right before approve via the YAML path
above.

### Inheritance on manual ticket creation

When you create a child ticket via the API (not via decompose), the
server applies the same rule as the YAML parser: if you don't specify
`persona` / `stack`, the new ticket inherits from its parent. See
`_resolve_persona_stack()` in `src/pravi/api/routes.py`. Inheritance
only happens at create time — later edits to the parent don't
propagate.

---

## Seeing the impact

Two endpoints make persona / stack assignment visible after the fact:

- `GET /api/spend/by-persona?window=7d` — total dev-run cost, run count,
  and distinct-ticket count, grouped by `tickets.persona`. NULL
  persona aggregates under `other`.
- `GET /api/spend/by-stack?window=7d` — same shape, grouped by
  `tickets.stack`. NULL stack aggregates under `unknown`.

The dashboard's **PersonaSpendCard** and **StackSpendCard** consume
these to render the per-persona / per-stack FinOps view. If the rollup looks noisy, it's
usually because too many tickets are landing without persona /
stack — either the architect isn't reaching for them or you're
clearing them on edit.

---

## Related reading

- [ADR 0004 — Agent personas (with stack specializations)](../adr/0004-agent-personas.md)
  — the design rationale, alternatives considered, and the full
  19-entry catalog with responsibilities / inputs / outputs.
- [ADR 0002 — LLM-agnostic architect, Claude-only dev](../adr/0002-llm-agnostic-architect-claude-only-dev.md)
  — the per-mode model pin that persona-keyed routing would extend.
- `src/pravi/personas/catalog.py` and `src/pravi/personas/stacks.py` —
  the single sources of truth.
- `src/pravi/prompts/decompose.py` — the architect prompt that surfaces
  the catalog during decomposition.
- `src/pravi/prompts/developer.py` — where persona + stack get
  composed into the dev agent's system prompt.

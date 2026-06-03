# ADR 0004 — Agent personas (with stack specializations)

- **Status:** Proposed
- **Date:** 2026-06-03
- **Deciders:** @cavanpage

## Context

Today pravi has two agent roles: the **architect** (read-only — clarify /
decompose / draft) and the **dev agent** (executes a plan via
claude-agent-sdk). Both run a generic system prompt with no notion of
*what kind of work this is* or *what tech it's in*. Every task gets the
same framing, whether it's "wire up a FastAPI route", "polish a React
component", or "write release notes".

Open questions this leaves on the table:

1. **Decompose quality.** When the architect breaks an epic into
   features + tasks, it's not asked to reason about the *kind* of skill
   each task needs. Tasks land at uneven granularity and the dev agent
   gets no role-specific framing.
2. **Sharper dev prompts.** A "frontend engineer" framing
   (component-first, accessibility, design tokens) is materially
   different from a "backend engineer" framing (boundary, schema,
   migration). One generic prompt has to compromise.
3. **Per-persona spend.** Pravi already aggregates ticket cost in the
   `budget/` rollup. We have no surface to answer "how much did the
   backend agent spend this week?" — a real product question once you're
   running pravi continuously.
4. **Stack specialization.** A "backend" task in Python wants a different
   skill loadout than a "backend" task in Java. Persona alone doesn't
   capture this — language/framework is an orthogonal axis.
5. **Cost-aware routing.** ADR 0002 already lets each architect mode pin
   its own model. Persona × stack extends that seam to the dev agent: a
   tech-writer task could route to Haiku; a Java backend task to Opus.

## Decision (proposed)

**Add two orthogonal fields on Ticket: `persona` (what kind of work) and
`stack` (what tech). The decompose architect assigns both. The dev
agent's system prompt is parameterized by both, and the resolved
`persona × stack` pair can declare a set of Claude Skills to load.**

### Two axes

| Axis | Examples | Source |
|---|---|---|
| `persona` | `architect`, `frontend`, `backend`, `tester`, `tech_writer`, … | Decompose architect assigns; manually editable |
| `stack` | `python-fastapi`, `python-django`, `java-spring`, `typescript-react`, `typescript-vue`, `go-stdlib`, `rust`, `markdown`, … | Inferred from `domains.yaml`, repo files, or assigned by architect |

`persona` is closed-set (the catalog below). `stack` is open-set —
anything the repo happens to be in. We ship a starter list of stack
slugs but the architect can mint new ones; the system treats unknown
stacks as `unknown` (no skills loaded, generic prompt).

### The 15-persona catalog: all implemented, status-gated

All 15 personas from the original spec ship as code with a `status`
field:

- **`active`** — wired into the decompose picker; the dev agent has a
  real persona-specific prompt modifier; Claude Skills loadout is
  defined (or explicitly empty if the persona doesn't need extra
  context).
- **`coming_soon`** — exists in the catalog and shows in the UI picker
  as a disabled chip ("coming soon"). The decompose architect is told
  *not* to pick these. If someone manually assigns one anyway, the dev
  agent falls back to the generic prompt and logs a warning.

| Persona | Group | Status (v1) | Why |
|---|---|---|---|
| `architect` | Architecture | active | Container-only design; output is the plan |
| `frontend` | Engineering | active | Real work pravi already does |
| `backend` | Engineering | active | Real work pravi already does |
| `tester` | Quality | active | Test-only nudge is well-scoped |
| `tech_writer` | Product | active | Cheap-model docs/release-notes target |
| `other` | — | active | Escape hatch (= today's generic dev prompt) |
| `product_manager` | Product | coming_soon | Needs PRD-shaped output capability we don't have |
| `ux_designer` | Product | coming_soon | No design-asset emission today |
| `dba` | Architecture | coming_soon | No schema-linter or migration-aware capability |
| `data_engineer` | Engineering | coming_soon | No ETL / pipeline templates |
| `ml_engineer` | Engineering | coming_soon | No training-loop or evaluation capability |
| `ai_fde` | Engineering | coming_soon | Overlaps with `backend`; carve out later |
| `perf_tester` | Quality | coming_soon | No load-testing scaffolding |
| `chaos_engineer` | Quality | coming_soon | No fault-injection capability |
| `pen_tester` | Quality | coming_soon | No scanner integration |
| `devops` | Platform | coming_soon | CI/IaC capability is its own scope |
| `sre` | Platform | coming_soon | Observability surface doesn't exist yet |
| `appsec` | Platform | coming_soon | Threat-model emission capability TBD |
| `finops` | Platform | coming_soon | Builds on the per-persona cost rollup |

The full catalog content (responsibilities / inputs / outputs) is the
appendix at the bottom of this ADR; the code-side catalog mirrors it.

### Claude Skills as the loadout mechanism

A `(persona, stack)` pair resolves to a list of **Claude Skills** the
dev agent activates. Skills are the right primitive because they're
already the way Claude composes domain expertise.

```
(backend, python-fastapi)       → ["python", "fastapi", "pytest"]
(backend, java-spring)          → ["java", "spring-boot", "junit"]
(frontend, typescript-react)    → ["typescript", "react", "tailwind"]
(tester, python-fastapi)        → ["pytest", "fastapi"]
(tech_writer, markdown)         → []      # no skills needed
(architect, *)                  → []      # planning-only persona
```

Two resolution rules:

1. **Persona-default + stack-additive.** Persona declares a baseline
   skill list; stack adds tech-specific skills. The dev agent sees the
   union.
2. **Stack inference order.** Stack comes from (a) explicit ticket
   field, (b) `domains.yaml` per-domain declaration, (c) auto-detected
   from repo files (pyproject.toml → python; package.json + react in
   deps → typescript-react; etc). First hit wins.

For v1 the skill loadout is *advisory* — the catalog records the
recommended skill names per `(persona, stack)`, but the dev agent's
system prompt nudges towards them rather than the SDK loading them
programmatically. This is forward-compat: once claude-agent-sdk grows
a clean skill-loading API, the wiring is a one-place change.

### Surface

- DB: `tickets.persona TEXT NULL` (slug), `tickets.stack TEXT NULL`
  (slug). Null persona = `other`; null stack = `unknown`. Alembic
  migration.
- Catalog: `src/pravi/personas/catalog.py` — single source of truth.
  Each `Persona` has `slug`, `name`, `group`, `status`, `description`,
  `system_prompt_modifier`, `baseline_skills: list[str]`.
- Stack catalog: `src/pravi/personas/stacks.py` — `Stack` has `slug`,
  `name`, `additional_skills: list[str]`, `detect: callable` (looks at
  the repo to decide).
- Decompose prompt: YAML output gains `persona:` and `stack:` per
  feature and per task. Parser tolerates unknown values.
- Developer prompt: `system_prompt()` takes optional `persona` and
  `stack` params and inserts a persona-specific paragraph + a
  recommended-skills hint.
- UI: persona chip on ticket row + ticket page; PersonaPicker with
  active personas selectable, coming-soon disabled (tooltip explains
  why). Stack inferred display; editable.
- Cost rollup: `budget/rollup.py` adds a `by_persona` and `by_stack`
  breakdown to `BudgetRollup`.

## Consequences

### Wins
- Decompose output reasons about both skill *kind* and *tech*.
- Dev agent gets sharper prompts and (eventually) a Skills loadout
  that matches both axes.
- Per-persona / per-stack cost rollup answers real FinOps questions
  with almost no new code.
- Forward-compatible with model-per-persona routing (Haiku for
  `tech_writer`, Opus for `architect`) once we have data on which
  combos actually run.
- All 15 personas visible in the UI as a roadmap. Users see what's
  coming; the decompose architect is fenced off from picking them.

### Costs (acknowledged)
- **Status drift.** A "coming_soon" persona that stays coming_soon for
  6 months becomes UI noise. Mitigation: status transitions are
  reviewed at each ADR revisit.
- **Stack misdetection.** Auto-detected stack is noisy in polyglot
  monorepos (a `pyproject.toml` next to a `package.json`). Per-domain
  declaration in `domains.yaml` overrides — recommended for any repo
  with >1 language.
- **Garbage-in for cost rollup.** If the architect mis-assigns
  persona, the rollup becomes noisy. Need to surface "manually
  reassigned" as a confidence signal.
- **Two new dropdowns** on every ticket edit form. Real friction for
  trivial tickets. Mitigation: both default sensibly from the parent
  / repo, so the user only touches them when overriding.

## Alternatives considered

### Ship only the 6 active personas; hide the other 9
Considered — was the original proposal. Rejected because surfacing the
full catalog with coming-soon status is a better roadmap signal for
the user *and* makes it cheaper to promote a persona to active later
(no UI work, no enum bump — just a status flip + a real prompt
modifier).

### Single combined `role` field, no orthogonal stack
Tempting because it's one less dropdown. Rejected because it forces
combinatorial explosion in the catalog (`backend_python_fastapi`,
`backend_java_spring`, …) and makes the cost-rollup view useless —
you'd want to see "backend spend across all stacks" *and* "Python
spend across all personas" and a flat combined field gives you neither.

### Persona as a *separate sub-agent* (own prompt, own tools)
Rejected (same as the original draft of this ADR): would mean N
parallel dev-agent codepaths with N transcripts and N budgets.
Persona-as-prompt-modifier on the same agent keeps everything in one
transcript.

### Wait for claude-agent-sdk skill-loading API before doing any of this
Rejected. The catalog + prompt-modifier + cost rollup pieces don't
depend on the SDK at all; only the *programmatic skill loading* does.
Build now, wire skills when the API lands.

## When to revisit

**Promote a persona from coming_soon → active when:**
- It has a real prompt modifier (not just a label) — a paragraph that
  the dev agent can actually use.
- It has a recommended skill list per common stack.
- There's a clear "what would this agent *do* differently from
  generic" answer.

**Demote a persona to coming_soon (or delete) if:**
- It's been active for N months and never assigned by the architect.
- Its rollup column is unread.

**Rethink the whole axis split if:**
- Stack and persona consistently co-vary in practice (e.g. the only
  `frontend` tasks are React, the only `backend` tasks are Python).
  Then collapse to a single combined field. Won't know until we have
  data.

## Related

- ADR [0002 — LLM-agnostic architect, Claude-only dev](0002-llm-agnostic-architect-claude-only-dev.md)
  — personas extend the per-mode model-override seam from architect to
  dev. The model picker can be persona-keyed once data justifies it.
- `src/pravi/personas/{catalog,stacks}.py` — the single sources of truth.
- `src/pravi/prompts/{decompose,developer}.py` — where the persona +
  stack params land.
- `src/pravi/budget/rollup.py` — the per-persona / per-stack
  breakdowns extend this.

---

## Appendix — Full persona catalog

The 15 personas, grouped by lifecycle stage. *Italicized* entries are
in the v1 starter set (status: active). The others ship as code but are
coming_soon.

### 1. Product and Definition

#### Product Manager
- **Responsibilities:** Requirements processing, user story creation,
  task prioritization. Primary interface for initial prompt processing.
- **Inputs:** Raw user prompts, stakeholder requests, business goals.
- **Outputs:** PRDs, user stories, acceptance criteria.
- *Status: coming_soon — needs PRD-shaped output capability.*

#### UX/UI Designer
- **Responsibilities:** Interface specifications, accessibility
  constraints, layout directives.
- **Inputs:** User stories, PRDs.
- **Outputs:** Wireframes (textual/JSON), styling guidelines, a11y
  checklists.
- *Status: coming_soon — no design-asset emission today.*

#### *Technical Writer*
- **Responsibilities:** Translates code/architecture/behavior into
  readable documentation.
- **Inputs:** Architectural diagrams, source code, API definitions.
- **Outputs:** OpenAPI specs, design docs, runbooks, user manuals.
- *Status: **active**. Defaults to a cheaper model.*

### 2. Architecture and Foundation

#### *Systems Architect* (slug: `architect`)
- **Responsibilities:** High-level system design, service boundaries,
  technology stack selection, trade-off analysis.
- **Inputs:** PRDs, non-functional requirements.
- **Outputs:** System architecture docs, API contracts, component
  diagrams (PlantUML/Mermaid).
- *Status: **active**. No file writes — output goes in the ticket body.*

#### Database Administrator / Storage Engineer
- **Responsibilities:** Schema design, query optimization, partitioning,
  state management.
- **Inputs:** System architecture, data requirements.
- **Outputs:** Database schemas (SQL/NoSQL), migration scripts, indexing
  strategies.
- *Status: coming_soon — no schema-linter / migration-aware capability.*

### 3. Engineering

#### *Frontend Engineer* (slug: `frontend`)
- **Responsibilities:** UI implementation, state management, client-side
  logic.
- **Inputs:** UX/UI specs, API contracts.
- **Outputs:** Client source (React/Vue/HTML/CSS), client tests.
- *Status: **active**. Real work pravi already does.*

#### *Backend Engineer* (slug: `backend`)
- **Responsibilities:** Business logic, API implementation, controller
  design.
- **Inputs:** System architecture, DB schemas, API contracts.
- **Outputs:** Server source, API endpoints, server tests.
- *Status: **active**. Real work pravi already does.*

#### Data Engineer
- **Responsibilities:** Stream processing, ETL pipelines, message
  brokers.
- **Inputs:** Data sources, analytics requirements.
- **Outputs:** Data pipelines, transformation scripts, warehouse schemas.
- *Status: coming_soon.*

#### Machine Learning Engineer
- **Responsibilities:** Model training, fine-tuning, dataset preparation.
- **Inputs:** Prepared data, model requirements.
- **Outputs:** Trained models, inference scripts, evaluation metrics.
- *Status: coming_soon — no training-loop / evaluation capability.*

#### AI / Forward Deployed Engineer
- **Responsibilities:** LLM orchestration, RAG pipelines, MCP integration.
- **Inputs:** Core application architecture, LLM interaction requirements.
- **Outputs:** Agentic workflows, prompt templates, vector DB integrations.
- *Status: coming_soon — overlaps with backend; carve out later.*

### 4. Quality and Validation

#### *Functional Tester* (slug: `tester`)
- **Responsibilities:** Unit, integration, e2e tests against acceptance
  criteria.
- **Inputs:** Acceptance criteria, compiled source.
- **Outputs:** Test suites, execution reports, bug tickets.
- *Status: **active**. Hard guardrail: don't change source outside
  `tests/`.*

#### Performance / Load Tester
- **Responsibilities:** Benchmarks + bottleneck analysis.
- **Inputs:** System architecture, expected loads.
- **Outputs:** Load scripts (k6, JMeter), performance reports.
- *Status: coming_soon.*

#### FMEA / Chaos Engineer
- **Responsibilities:** Fault injection, resilience strategies.
- **Inputs:** System architecture, infrastructure definitions.
- **Outputs:** Chaos experiments, FMEA reports.
- *Status: coming_soon.*

#### Penetration Tester
- **Responsibilities:** Active exploits against generated code +
  configs.
- **Inputs:** Application source, deployed environments.
- **Outputs:** Vulnerability reports, exploit PoCs, remediation steps.
- *Status: coming_soon — needs scanner integration.*

### 5. Platform, Operations, and Security

#### DevOps / Platform Engineer
- **Responsibilities:** CI/CD pipelines, container orchestration,
  deployment manifests.
- **Inputs:** Source code, infra requirements.
- **Outputs:** Dockerfiles, K8s manifests, CI pipelines.
- *Status: coming_soon.*

#### Site Reliability Engineer (SRE)
- **Responsibilities:** Observability, telemetry, SLIs/SLOs, system
  health.
- **Inputs:** Deployed app metrics, availability requirements.
- **Outputs:** Alerting rules, Grafana/Prometheus configs, incident
  response.
- *Status: coming_soon.*

#### AppSec Engineer
- **Responsibilities:** Shift-left security (IAM, auth, encryption) at
  code-gen time.
- **Inputs:** System architecture, proposed dependencies.
- **Outputs:** Code security audits, IAM policies, threat models.
- *Status: coming_soon.*

#### Compliance / FinOps
- **Responsibilities:** Regulatory compliance (SOC2, GDPR), cost
  optimization.
- **Inputs:** Infra definitions, data handling protocols.
- **Outputs:** Cost estimates, compliance checklists, resource tagging.
- *Status: coming_soon — builds on the per-persona rollup once it ships.*

## Appendix — Stack starter list

Open-set, but these are the slugs the v1 catalog ships with. The
decompose architect can mint new ones; unknown stacks resolve to
`unknown` (no skills loaded).

| Slug | Detect signals | Suggested skills (additive on top of persona) |
|---|---|---|
| `python-fastapi` | `pyproject.toml` + `fastapi` in deps | `python`, `fastapi`, `pytest` |
| `python-django` | `manage.py` + `django` in deps | `python`, `django`, `pytest` |
| `python-stdlib` | `pyproject.toml`, no web framework | `python`, `pytest` |
| `typescript-react` | `package.json` + `react` in deps | `typescript`, `react` |
| `typescript-vue` | `package.json` + `vue` in deps | `typescript`, `vue` |
| `typescript-node` | `package.json`, no UI framework | `typescript`, `node` |
| `java-spring` | `pom.xml` or `build.gradle` + `spring` | `java`, `spring-boot`, `junit` |
| `go-stdlib` | `go.mod` | `go`, `go-test` |
| `rust` | `Cargo.toml` | `rust`, `cargo-test` |
| `markdown` | persona is `tech_writer` | _(none — plain markdown skill)_ |
| `unknown` | fallthrough | _(none — generic dev prompt)_ |

Skill names are *advisory* in v1 — they're recorded on the catalog
entries and surfaced in the dev agent's system prompt as a hint, but
not programmatically loaded by claude-agent-sdk yet. When the SDK
grows a clean skill-loading API, this becomes the wire.

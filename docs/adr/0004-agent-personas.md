# ADR 0004 — Agent personas (with stack specializations)

- **Status:** Accepted
- **Date:** 2026-06-03 (proposed) · 2026-06-04 (accepted)
- **Deciders:** @cavanpage

## Context

Before this ADR, pravi had two agent roles: the **architect** (read-only —
clarify / decompose / draft) and the **dev agent** (executes a plan via
claude-agent-sdk). Both ran a generic system prompt with no notion of
*what kind of work this is* or *what tech it's in*. Every task got the
same framing, whether it was "wire up a FastAPI route", "polish a React
component", or "write release notes".

Open questions this left on the table:

1. **Decompose quality.** When the architect broke an epic into
   features + tasks, it was not asked to reason about the *kind* of
   skill each task needed. Tasks landed at uneven granularity and the
   dev agent got no role-specific framing.
2. **Sharper dev prompts.** A "frontend engineer" framing
   (component-first, accessibility, design tokens) is materially
   different from a "backend engineer" framing (boundary, schema,
   migration). One generic prompt had to compromise.
3. **Per-persona spend.** Pravi already aggregates ticket cost in the
   `budget/` rollup. There was no surface to answer "how much did the
   backend agent spend this week?" — a real product question once
   pravi is running continuously.
4. **Stack specialization.** A "backend" task in Python wants a
   different skill loadout than a "backend" task in Java. Persona
   alone does not capture this — language/framework is an orthogonal
   axis.
5. **Cost-aware routing.** ADR 0002 already lets each architect mode
   pin its own model. Persona × stack extends that seam to the dev
   agent: a tech-writer task can route to Haiku; a Java backend task
   to Opus.

## Decision

**Two orthogonal fields on Ticket: `persona` (what kind of work) and
`stack` (what tech). The decompose architect assigns both. The dev
agent's system prompt is parameterized by both, and the resolved
`persona × stack` pair declares a set of Claude Skills to load.**

### Two axes

| Axis      | Examples                                                                                       | Source                                                                  |
|-----------|------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------|
| `persona` | `architect`, `frontend`, `backend`, `tester`, `tech_writer`, `other`                            | Decompose architect assigns; manually editable                          |
| `stack`   | `python-fastapi`, `python-django`, `typescript-react`, `java-spring`, `go-stdlib`, `markdown`, … | Inferred from `domains.yaml`, repo files, or assigned by architect      |

`persona` is closed-set (the catalog below). `stack` is open-set —
anything the repo happens to be in. The v1 catalog ships a starter
stack list but the architect can mint new ones; unknown stacks resolve
to `unknown` (no skills loaded, generic prompt).

### The persona catalog: all implemented, status-gated

The catalog ships 19 personas as code, all with a `status` field:

- **`active`** (6 personas) — wired into the decompose picker; the dev
  agent has a real persona-specific prompt modifier; Claude Skills
  loadout is defined (or explicitly empty if the persona doesn't need
  extra context).
- **`coming_soon`** (13 personas) — exists in the catalog and shows in
  the UI picker as a disabled chip ("coming soon"). The decompose
  architect is told *not* to pick these. If someone manually assigns
  one anyway, `get_persona()` still resolves it normally but the dev
  agent falls back to the generic prompt (empty
  `system_prompt_modifier`) and logs a warning.

| Persona            | Group         | Status (v1)  | Why                                                       |
|--------------------|---------------|--------------|-----------------------------------------------------------|
| `architect`        | architecture  | active       | Container-only design; output is the plan                 |
| `frontend`         | engineering   | active       | Real work pravi already does                              |
| `backend`          | engineering   | active       | Real work pravi already does                              |
| `tester`           | quality       | active       | Test-only nudge is well-scoped                            |
| `tech_writer`      | product       | active       | Cheap-model docs / release-notes target                   |
| `other`            | other         | active       | Escape hatch (= today's generic dev prompt)               |
| `product_manager`  | product       | coming_soon  | Needs PRD-shaped output capability not yet present        |
| `ux_designer`      | product       | coming_soon  | No design-asset emission today                            |
| `dba`              | architecture  | coming_soon  | No schema-linter / migration-aware capability             |
| `data_engineer`    | engineering   | coming_soon  | No ETL / pipeline templates                               |
| `ml_engineer`      | engineering   | coming_soon  | No training-loop / evaluation capability                  |
| `ai_fde`           | engineering   | coming_soon  | Overlaps with `backend`; carve out later                  |
| `perf_tester`      | quality       | coming_soon  | No load-testing scaffolding                               |
| `chaos_engineer`   | quality       | coming_soon  | No fault-injection capability                             |
| `pen_tester`       | quality       | coming_soon  | No scanner integration                                    |
| `devops`           | platform      | coming_soon  | CI / IaC capability is its own scope                      |
| `sre`              | platform      | coming_soon  | Observability surface doesn't exist yet                   |
| `appsec`           | platform      | coming_soon  | Threat-model emission capability TBD                      |
| `finops`           | platform      | coming_soon  | Builds on the per-persona cost rollup                     |

The full catalog content (responsibilities / inputs / outputs) is the
appendix at the bottom of this ADR; the code-side catalog in
`src/pravi/personas/catalog.py` mirrors it.

### Claude Skills as the loadout mechanism

A `(persona, stack)` pair resolves to a list of **Claude Skills** the
dev agent activates. Skills are the right primitive because they are
already the way Claude composes domain expertise.

```
(backend, python-fastapi)       → ["python", "fastapi", "pytest"]
(backend, java-spring)          → ["java", "spring-boot", "junit"]
(frontend, typescript-react)    → ["typescript", "react"]
(tester, python-fastapi)        → ["python", "fastapi", "pytest"]
(tech_writer, markdown)         → []      # no skills needed
(architect, *)                  → []      # planning-only persona
```

Two resolution rules:

1. **Persona-default + stack-additive.** Persona declares a
   `baseline_skills` list; stack adds `additional_skills`. The dev
   agent sees the union. In v1 every active persona ships with
   `baseline_skills=[]`, so the practical loadout is currently
   stack-driven.
2. **Stack inference order.** Stack comes from (a) explicit ticket
   field, (b) `domains.yaml` per-domain declaration, (c) repo-file
   hints recorded as `detect_hints` on each `Stack` entry. First hit
   wins. The hints are advisory documentation, not executable
   auto-detection — a real detector remains a follow-up.

For v1 the skill loadout is *advisory* — the catalog records the
recommended skill names per `(persona, stack)`, but the dev agent's
system prompt nudges towards them rather than the SDK loading them
programmatically. This is forward-compat: once claude-agent-sdk grows
a clean skill-loading API, the wiring is a one-place change.

### Surface (as shipped)

- DB: `tickets.persona TEXT NULL` (slug), `tickets.stack TEXT NULL`
  (slug). Null persona resolves to `other`; null stack resolves to
  `unknown`. Alembic migration applied.
- Catalog: `src/pravi/personas/catalog.py` — single source of truth.
  `Persona` is a frozen dataclass with `slug`, `name`, `group`
  (`PersonaGroup` StrEnum: `product`, `architecture`, `engineering`,
  `quality`, `platform`, `other`), `status` (`PersonaStatus` StrEnum:
  `active`, `coming_soon`), `description`, `system_prompt_modifier`,
  and `baseline_skills`. Module exports `ALL_PERSONAS`,
  `ACTIVE_PERSONAS`, `DEFAULT_PERSONA` (= `other`), and `get_persona()`.
- Stack catalog: `src/pravi/personas/stacks.py` — `Stack` has `slug`,
  `name`, `description`, `additional_skills`, and `detect_hints`
  (a list of strings, advisory only). Module exports `KNOWN_STACKS`,
  `DEFAULT_STACK` (= `unknown`), and `get_stack()`.
- Decompose prompt: YAML output gains `persona:` and `stack:` per
  feature and per task. Parser tolerates unknown values (they resolve
  via the defaults).
- Developer prompt: `system_prompt()` takes optional `persona` and
  `stack` params and inserts the persona's `system_prompt_modifier` +
  a recommended-skills hint built from the union of
  `baseline_skills` and `additional_skills`.
- UI: persona chip on ticket row + ticket page; PersonaPicker with
  active personas selectable, coming-soon disabled (tooltip explains
  why). Persona/stack are set at creation (decompose or the new-ticket
  form); in-place edit is still roadmap.
- Cost rollup: `budget/by_persona.py` provides `aggregate_by_persona`
  and `aggregate_by_stack`, surfaced as `GET /api/spend/by-persona`
  and `/api/spend/by-stack`.

## Consequences

### Wins
- Decompose output reasons about both skill *kind* and *tech*.
- Dev agent gets sharper prompts and (eventually) a Skills loadout
  that matches both axes.
- Per-persona / per-stack cost rollup answers real FinOps questions
  with almost no new code.
- Forward-compatible with model-per-persona routing (Haiku for
  `tech_writer`, Opus for `architect`) once data justifies it.
- All 19 personas visible in the UI as a roadmap; the decompose
  architect is fenced off from picking the inactive ones.

### Costs (acknowledged)
- **Status drift.** A `coming_soon` persona that stays `coming_soon`
  for 6 months becomes UI noise. Mitigation: status transitions are
  reviewed at each ADR revisit.
- **Stack misdetection.** Detection is advisory in v1 (`detect_hints`
  is documentation, not code). Polyglot monorepos still need a
  per-domain declaration in `domains.yaml`.
- **Garbage-in for cost rollup.** If the architect mis-assigns
  persona, the rollup becomes noisy. Need to surface "manually
  reassigned" as a confidence signal.
- **Two new dropdowns** on every ticket edit form. Real friction for
  trivial tickets. Mitigation: both default sensibly from the parent
  / repo, so the user only touches them when overriding.

## Alternatives considered

### Ship only the 6 active personas; hide the other 13
Considered — was the original proposal. Rejected because surfacing the
full catalog with coming-soon status is a better roadmap signal *and*
makes it cheaper to promote a persona to active later (no UI work, no
enum bump — just a status flip + a real prompt modifier).

### Single combined `role` field, no orthogonal stack
Tempting because it's one less dropdown. Rejected because it forces
combinatorial explosion in the catalog (`backend_python_fastapi`,
`backend_java_spring`, …) and makes the cost-rollup view useless —
"backend spend across all stacks" *and* "Python spend across all
personas" are both desired and a flat combined field gives neither.

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

**Promote a persona from `coming_soon` → `active` when:**
- It has a real prompt modifier (not just a label) — a paragraph the
  dev agent can actually use.
- It has a recommended skill list per common stack.
- There is a clear "what would this agent *do* differently from
  generic" answer.

**Demote a persona to `coming_soon` (or delete) if:**
- It has been `active` for N months and never assigned by the
  architect.
- Its rollup column is unread.

**Rethink the whole axis split if:**
- Stack and persona consistently co-vary in practice (e.g. the only
  `frontend` tasks are React, the only `backend` tasks are Python).
  Then collapse to a single combined field. Won't know until there is
  data.

## Related

- ADR [0002 — LLM-agnostic architect, Claude-only dev](0002-llm-agnostic-architect-claude-only-dev.md)
  — personas extend the per-mode model-override seam from architect to
  dev. The model picker can be persona-keyed once data justifies it.
- `src/pravi/personas/{catalog,stacks}.py` — the single sources of
  truth.
- `src/pravi/prompts/{decompose,developer}.py` — where the persona +
  stack params land.
- `src/pravi/budget/by_persona.py` — the per-persona / per-stack
  spend aggregations.

---

## Appendix — Full persona catalog

The 19 personas, grouped by lifecycle stage. *Italicized* entries are
in the v1 starter set (status: `active`). The others ship as code but
are `coming_soon`.

### 1. Product and Definition

#### Product Manager
- **Responsibilities:** Requirements processing, user story creation,
  task prioritization. Primary interface for initial prompt
  processing.
- **Inputs:** Raw user prompts, stakeholder requests, business goals.
- **Outputs:** PRDs, user stories, acceptance criteria.
- *Status: `coming_soon` — needs PRD-shaped output capability.*

#### UX/UI Designer
- **Responsibilities:** Interface specifications, accessibility
  constraints, layout directives.
- **Inputs:** User stories, PRDs.
- **Outputs:** Wireframes (textual / JSON), styling guidelines, a11y
  checklists.
- *Status: `coming_soon` — no design-asset emission today.*

#### *Technical Writer* (slug: `tech_writer`)
- **Responsibilities:** Translates code / architecture / behavior into
  readable documentation.
- **Inputs:** Architectural diagrams, source code, API definitions.
- **Outputs:** OpenAPI specs, design docs, runbooks, user manuals.
- *Status: **`active`**. Defaults to a cheaper model.*

### 2. Architecture and Foundation

#### *Systems Architect* (slug: `architect`)
- **Responsibilities:** High-level system design, service boundaries,
  technology stack selection, trade-off analysis.
- **Inputs:** PRDs, non-functional requirements.
- **Outputs:** System architecture docs, API contracts, component
  diagrams (PlantUML / Mermaid).
- *Status: **`active`**. No file writes — output goes in the ticket
  body or an ADR.*

#### Database Administrator / Storage Engineer (slug: `dba`)
- **Responsibilities:** Schema design, query optimization,
  partitioning, state management.
- **Inputs:** System architecture, data requirements.
- **Outputs:** Database schemas (SQL / NoSQL), migration scripts,
  indexing strategies.
- *Status: `coming_soon` — no schema-linter / migration-aware
  capability.*

### 3. Engineering

#### *Frontend Engineer* (slug: `frontend`)
- **Responsibilities:** UI implementation, state management,
  client-side logic.
- **Inputs:** UX/UI specs, API contracts.
- **Outputs:** Client source (React / Vue / HTML / CSS), client tests.
- *Status: **`active`**. Real work pravi already does.*

#### *Backend Engineer* (slug: `backend`)
- **Responsibilities:** Business logic, API implementation, controller
  design.
- **Inputs:** System architecture, DB schemas, API contracts.
- **Outputs:** Server source, API endpoints, server tests.
- *Status: **`active`**. Real work pravi already does.*

#### Data Engineer (slug: `data_engineer`)
- **Responsibilities:** Stream processing, ETL pipelines, message
  brokers.
- **Inputs:** Data sources, analytics requirements.
- **Outputs:** Data pipelines, transformation scripts, warehouse
  schemas.
- *Status: `coming_soon`.*

#### Machine Learning Engineer (slug: `ml_engineer`)
- **Responsibilities:** Model training, fine-tuning, dataset
  preparation.
- **Inputs:** Prepared data, model requirements.
- **Outputs:** Trained models, inference scripts, evaluation metrics.
- *Status: `coming_soon` — no training-loop / evaluation capability.*

#### AI / Forward Deployed Engineer (slug: `ai_fde`)
- **Responsibilities:** LLM orchestration, RAG pipelines, MCP
  integration.
- **Inputs:** Core application architecture, LLM interaction
  requirements.
- **Outputs:** Agentic workflows, prompt templates, vector DB
  integrations.
- *Status: `coming_soon` — overlaps with `backend`; carve out later.*

### 4. Quality and Validation

#### *Functional Tester* (slug: `tester`)
- **Responsibilities:** Unit, integration, e2e tests against
  acceptance criteria.
- **Inputs:** Acceptance criteria, compiled source.
- **Outputs:** Test suites, execution reports, bug tickets.
- *Status: **`active`**. Hard guardrail: don't change source outside
  `tests/`.*

#### Performance / Load Tester (slug: `perf_tester`)
- **Responsibilities:** Benchmarks + bottleneck analysis.
- **Inputs:** System architecture, expected loads.
- **Outputs:** Load scripts (k6, JMeter), performance reports.
- *Status: `coming_soon`.*

#### FMEA / Chaos Engineer (slug: `chaos_engineer`)
- **Responsibilities:** Fault injection, resilience strategies.
- **Inputs:** System architecture, infrastructure definitions.
- **Outputs:** Chaos experiments, FMEA reports.
- *Status: `coming_soon`.*

#### Penetration Tester (slug: `pen_tester`)
- **Responsibilities:** Active exploits against generated code +
  configs.
- **Inputs:** Application source, deployed environments.
- **Outputs:** Vulnerability reports, exploit PoCs, remediation steps.
- *Status: `coming_soon` — needs scanner integration.*

### 5. Platform, Operations, and Security

#### DevOps / Platform Engineer (slug: `devops`)
- **Responsibilities:** CI / CD pipelines, container orchestration,
  deployment manifests.
- **Inputs:** Source code, infra requirements.
- **Outputs:** Dockerfiles, K8s manifests, CI pipelines.
- *Status: `coming_soon`.*

#### Site Reliability Engineer (slug: `sre`)
- **Responsibilities:** Observability, telemetry, SLIs / SLOs, system
  health.
- **Inputs:** Deployed app metrics, availability requirements.
- **Outputs:** Alerting rules, Grafana / Prometheus configs, incident
  response.
- *Status: `coming_soon`.*

#### AppSec Engineer (slug: `appsec`)
- **Responsibilities:** Shift-left security (IAM, auth, encryption) at
  code-gen time.
- **Inputs:** System architecture, proposed dependencies.
- **Outputs:** Code security audits, IAM policies, threat models.
- *Status: `coming_soon`.*

#### Compliance / FinOps (slug: `finops`)
- **Responsibilities:** Regulatory compliance (SOC2, GDPR), cost
  optimization.
- **Inputs:** Infra definitions, data handling protocols.
- **Outputs:** Cost estimates, compliance checklists, resource
  tagging.
- *Status: `coming_soon` — builds on the per-persona rollup once it
  ships.*

### 6. Escape hatch

#### *Other / generic* (slug: `other`)
- **Responsibilities:** No persona-specific framing. The generic dev
  agent prompt — used when none of the above fit.
- **Inputs:** Whatever the ticket has.
- **Outputs:** Whatever the ticket asks for.
- *Status: **`active`**. Default for any ticket with null persona.*

## Appendix — Stack starter list

Open-set, but these are the slugs the v1 catalog ships with in
`src/pravi/personas/stacks.py`. The decompose architect can mint new
ones; unknown stacks resolve to `unknown` (no skills loaded).

| Slug              | Detect hints (advisory)                          | Additional skills (added to persona baseline) |
|-------------------|--------------------------------------------------|-----------------------------------------------|
| `python-fastapi`  | `pyproject.toml` with `fastapi` in deps          | `python`, `fastapi`, `pytest`                 |
| `python-django`   | `manage.py` + `django` in deps                   | `python`, `django`, `pytest`                  |
| `python-stdlib`   | `pyproject.toml` without a web framework         | `python`, `pytest`                            |
| `typescript-react`| `package.json` with `react` in deps              | `typescript`, `react`                         |
| `typescript-vue`  | `package.json` with `vue` in deps                | `typescript`, `vue`                           |
| `typescript-node` | `package.json`, no UI framework                  | `typescript`, `node`                          |
| `java-spring`     | `pom.xml` or `build.gradle` with `spring` deps   | `java`, `spring-boot`, `junit`                |
| `go-stdlib`       | `go.mod`                                         | `go`, `go-test`                               |
| `rust`            | `Cargo.toml`                                     | `rust`, `cargo-test`                          |
| `markdown`        | `persona=tech_writer`                            | _(none — plain markdown)_                     |
| `unknown`         | fallthrough                                      | _(none — generic dev prompt)_                 |

Skill names are *advisory* in v1 — they are recorded on the catalog
entries and surfaced in the dev agent's system prompt as a hint, but
not programmatically loaded by claude-agent-sdk yet. The `detect_hints`
strings are documentation, not executable detection; a real detector
remains a follow-up. When the SDK grows a clean skill-loading API and
the detector lands, the wires connect in one place each.

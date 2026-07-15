"""The 19-persona catalog (6 active, 13 coming-soon) — see ADR 0004 for the design.

All entries are coded; the `status` field gates whether the decompose
architect is allowed to pick a persona. Active personas have real
`system_prompt_modifier` text + a `baseline_skills` list. Coming-soon
personas exist as labels for the roadmap UI but the dev agent falls
back to the generic prompt if one is assigned anyway.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class PersonaStatus(StrEnum):
    active = "active"
    coming_soon = "coming_soon"


class PersonaGroup(StrEnum):
    product = "product"
    architecture = "architecture"
    engineering = "engineering"
    quality = "quality"
    platform = "platform"
    other = "other"


@dataclass(frozen=True)
class Persona:
    slug: str
    name: str
    group: PersonaGroup
    status: PersonaStatus
    description: str
    # One-paragraph framing inserted into the dev agent's system prompt
    # when this persona is assigned. Empty for coming_soon personas
    # (they fall back to the generic prompt with a warning).
    system_prompt_modifier: str = ""
    # Claude Skills (by name) recommended for this persona regardless of
    # stack. Stack-specific skills are added on top — see `stacks.py`.
    # Advisory in v1: surfaced in the system prompt as a hint, not
    # loaded by the SDK programmatically.
    baseline_skills: list[str] = field(default_factory=list)


# ---- v1 active personas ---------------------------------------------------


_ARCHITECT = Persona(
    slug="architect",
    name="Systems Architect",
    group=PersonaGroup.architecture,
    status=PersonaStatus.active,
    description=(
        "High-level system design: service boundaries, technology stack "
        "selection, trade-off analysis. Output is the plan/spec, not "
        "committed code."
    ),
    system_prompt_modifier=(
        "Operate as a systems architect. Do NOT write or edit source "
        "files. Your output is the design itself — service boundaries, "
        "API contracts, and trade-off rationale captured in the ticket "
        "body or an ADR file under `docs/`. Prefer reading widely and "
        "diagramming over coding."
    ),
    baseline_skills=[],
)

_FRONTEND = Persona(
    slug="frontend",
    name="Frontend Engineer",
    group=PersonaGroup.engineering,
    status=PersonaStatus.active,
    description=(
        "UI implementation, state management, client-side logic. "
        "Component composition, accessibility, design tokens."
    ),
    system_prompt_modifier=(
        "Operate as a frontend engineer. Prefer component composition "
        "over inline markup; respect existing design tokens / theme "
        "variables; ensure interactive elements have accessible names "
        "and keyboard handlers. Keep client state colocated and avoid "
        "redundant global state. Write a focused client-side test for "
        "behavior that isn't trivially visible from the markup."
    ),
    baseline_skills=[],
)

_BACKEND = Persona(
    slug="backend",
    name="Backend Engineer",
    group=PersonaGroup.engineering,
    status=PersonaStatus.active,
    description=(
        "Business logic, API implementation, controller design. "
        "Boundary thinking, schema discipline, test-first."
    ),
    system_prompt_modifier=(
        "Operate as a backend engineer. Think in boundaries: handlers "
        "stay thin, business logic in services, persistence in repos. "
        "Schema changes are migrations, never edits to existing ones. "
        "Add a focused test that exercises the boundary you changed — "
        "happy path AND one error path."
    ),
    baseline_skills=[],
)

_TESTER = Persona(
    slug="tester",
    name="Functional Tester",
    group=PersonaGroup.quality,
    status=PersonaStatus.active,
    description=(
        "Unit, integration, and e2e tests against acceptance criteria. "
        "Test-only changes — no source edits outside tests/."
    ),
    system_prompt_modifier=(
        "Operate as a functional tester. **You must not change source "
        "code outside `tests/` (or its language-specific equivalent — "
        "`__tests__`, `*_test.go`, etc).** If a test fails because the "
        "source has a bug, document the bug in the ticket body and stop; "
        "do not fix it here. Write tests that read like specifications "
        "of behavior, with one assertion per concept."
    ),
    baseline_skills=[],
)

_TECH_WRITER = Persona(
    slug="tech_writer",
    name="Technical Writer",
    group=PersonaGroup.product,
    status=PersonaStatus.active,
    description=(
        "Translates code / architecture / behavior into readable docs. Defaults to a cheaper model."
    ),
    system_prompt_modifier=(
        "Operate as a technical writer. Output is markdown — READMEs, "
        "design docs, release notes, ADRs. Prefer concrete examples over "
        "abstract description. Surface the *why* alongside the *what*. "
        "Don't touch source code; if a doc needs source changes to be "
        "accurate, note them in the ticket body and stop."
    ),
    baseline_skills=[],
)

_OTHER = Persona(
    slug="other",
    name="Other (generic)",
    group=PersonaGroup.other,
    status=PersonaStatus.active,
    description=("Escape hatch — no persona-specific framing. Today's generic dev agent prompt."),
    system_prompt_modifier="",
    baseline_skills=[],
)


# ---- coming_soon personas (shown in UI, blocked from decompose) -----------


def _soon(
    slug: str,
    name: str,
    group: PersonaGroup,
    description: str,
) -> Persona:
    """Sugar for the coming_soon entries — same shape, no modifier."""
    return Persona(
        slug=slug,
        name=name,
        group=group,
        status=PersonaStatus.coming_soon,
        description=description,
        system_prompt_modifier="",
        baseline_skills=[],
    )


_PRODUCT_MANAGER = _soon(
    "product_manager",
    "Product Manager",
    PersonaGroup.product,
    "Requirements processing, user story creation, task prioritization.",
)
_UX_DESIGNER = _soon(
    "ux_designer",
    "UX/UI Designer",
    PersonaGroup.product,
    "Interface specs, accessibility constraints, layout directives.",
)
_DBA = _soon(
    "dba",
    "Database Administrator / Storage Engineer",
    PersonaGroup.architecture,
    "Schema design, query optimization, partitioning, state management.",
)
_DATA_ENGINEER = _soon(
    "data_engineer",
    "Data Engineer",
    PersonaGroup.engineering,
    "Stream processing, ETL pipelines, message brokers.",
)
_ML_ENGINEER = _soon(
    "ml_engineer",
    "Machine Learning Engineer",
    PersonaGroup.engineering,
    "Model training, fine-tuning, dataset preparation.",
)
_AI_FDE = _soon(
    "ai_fde",
    "AI / Forward Deployed Engineer",
    PersonaGroup.engineering,
    "LLM orchestration, RAG pipelines, MCP integration.",
)
_PERF_TESTER = _soon(
    "perf_tester",
    "Performance / Load Tester",
    PersonaGroup.quality,
    "Benchmarks + bottleneck analysis.",
)
_CHAOS_ENGINEER = _soon(
    "chaos_engineer",
    "FMEA / Chaos Engineer",
    PersonaGroup.quality,
    "Fault injection, resilience strategies, FMEA reports.",
)
_PEN_TESTER = _soon(
    "pen_tester",
    "Penetration Tester",
    PersonaGroup.quality,
    "Active exploits against generated code + configs.",
)
_DEVOPS = _soon(
    "devops",
    "DevOps / Platform Engineer",
    PersonaGroup.platform,
    "CI/CD pipelines, container orchestration, deployment manifests.",
)
_SRE = _soon(
    "sre",
    "Site Reliability Engineer",
    PersonaGroup.platform,
    "Observability, telemetry, SLIs/SLOs, system health.",
)
_APPSEC = _soon(
    "appsec",
    "AppSec Engineer",
    PersonaGroup.platform,
    "Shift-left security: IAM, auth, encryption at code-gen time.",
)
_FINOPS = _soon(
    "finops",
    "Compliance / FinOps",
    PersonaGroup.platform,
    "Regulatory compliance (SOC2, GDPR) + cost optimization.",
)


# ---- catalog --------------------------------------------------------------


ALL_PERSONAS: list[Persona] = [
    # active (6)
    _ARCHITECT,
    _FRONTEND,
    _BACKEND,
    _TESTER,
    _TECH_WRITER,
    _OTHER,
    # coming_soon (13) — kept in roughly the ADR's group order
    _PRODUCT_MANAGER,
    _UX_DESIGNER,
    _DBA,
    _DATA_ENGINEER,
    _ML_ENGINEER,
    _AI_FDE,
    _PERF_TESTER,
    _CHAOS_ENGINEER,
    _PEN_TESTER,
    _DEVOPS,
    _SRE,
    _APPSEC,
    _FINOPS,
]


ACTIVE_PERSONAS: list[Persona] = [p for p in ALL_PERSONAS if p.status is PersonaStatus.active]


_BY_SLUG: dict[str, Persona] = {p.slug: p for p in ALL_PERSONAS}

DEFAULT_PERSONA: Persona = _OTHER


def get_persona(slug: str | None) -> Persona:
    """Resolve a slug to a Persona. Unknown or null → DEFAULT_PERSONA
    (= `other`, generic prompt). Coming-soon personas resolve normally —
    callers decide what to do with their inactive status."""
    if not slug:
        return DEFAULT_PERSONA
    return _BY_SLUG.get(slug, DEFAULT_PERSONA)

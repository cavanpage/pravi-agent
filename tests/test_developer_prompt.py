"""Developer system_prompt() assembly — see ADR 0004.

Catches: reordering the prompt that drops the persona block; a coming_soon
entry sneaking a modifier in; the skills-list dedup regressing into
duplicates or empty output.
"""
from __future__ import annotations

# Reused across tests — keep the boilerplate compact.
_BASE_KWARGS = dict(
    repo_name="example",
    domain_name="shared",
    domain_description="shared utilities",
    domain_paths=["packages/shared/"],
    cwd="/tmp/wt",
)


def test_persona_modifier_and_skills_hint_appended():
    from pravi.prompts.developer import system_prompt

    sp = system_prompt(
        **_BASE_KWARGS,
        persona="backend",
        stack="python-fastapi",
    )
    assert "backend engineer" in sp.lower()
    # Recommended-skills hint surfaces the union of baseline + stack-additional
    # skills. backend.baseline=[]; python-fastapi.additional=[python, fastapi, pytest].
    for skill in ("python", "fastapi", "pytest"):
        assert f"`{skill}`" in sp, f"recommended skill {skill!r} missing from prompt"


def test_coming_soon_persona_falls_back_to_generic():
    from pravi.prompts.developer import system_prompt

    sp = system_prompt(**_BASE_KWARGS, persona="pen_tester", stack=None)
    # No persona block AT ALL — pen_tester has an empty modifier so the
    # whole block is suppressed; the prompt should not even mention it.
    assert "Persona —" not in sp
    assert "Recommended Claude Skills" not in sp


def test_no_persona_equals_other_persona():
    from pravi.prompts.developer import system_prompt

    a = system_prompt(**_BASE_KWARGS, persona=None, stack=None)
    b = system_prompt(**_BASE_KWARGS, persona="other", stack=None)
    # `other` is the escape hatch — has an empty modifier; output must
    # equal the no-persona case so users see a consistent baseline.
    assert a == b


def test_tester_persona_includes_test_only_guardrail():
    """The whole point of the tester persona is the "no source outside
    tests/" rule. If that text drifts out, the dev agent will start
    editing source while tagged as a tester — silent quality regression."""
    from pravi.prompts.developer import system_prompt

    sp = system_prompt(**_BASE_KWARGS, persona="tester", stack=None)
    assert "tests/" in sp
    assert "must not change source code" in sp.lower() or "no source" in sp.lower()


def test_unknown_stack_yields_no_extra_skills():
    """Open-set: unknown stack slugs resolve to `unknown` (no additional
    skills). Persona's baseline still applies."""
    from pravi.prompts.developer import system_prompt

    sp_known = system_prompt(**_BASE_KWARGS, persona="backend", stack="python-fastapi")
    sp_unknown = system_prompt(**_BASE_KWARGS, persona="backend", stack="cobol-90")
    # The known one names `fastapi`; the unknown one shouldn't.
    assert "fastapi" in sp_known
    assert "fastapi" not in sp_unknown


def test_skills_hint_omitted_when_no_skills_resolved():
    """Architect persona has empty baseline + architect work has no
    stack-recommended skills today. The skills hint paragraph should be
    suppressed entirely rather than printing 'Recommended Claude Skills: '."""
    from pravi.prompts.developer import system_prompt

    sp = system_prompt(**_BASE_KWARGS, persona="architect", stack=None)
    # Persona modifier still present
    assert "architect" in sp.lower()
    # But no skills line
    assert "Recommended Claude Skills" not in sp

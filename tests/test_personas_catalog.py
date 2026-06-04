"""Persona catalog resolution — see ADR 0004.

Catches catalog-shape regressions: someone removes a slug, renames one,
accidentally flips an active/coming_soon status, or breaks the lookup map.
"""
from __future__ import annotations


def test_get_persona_known_active_has_modifier():
    from pravi.personas import PersonaStatus, get_persona

    p = get_persona("backend")
    assert p.slug == "backend"
    assert p.status is PersonaStatus.active
    # Active personas earn their label by having a real modifier.
    assert p.system_prompt_modifier, "active persona must have a non-empty modifier"


def test_get_persona_coming_soon_has_empty_modifier():
    from pravi.personas import PersonaStatus, get_persona

    p = get_persona("pen_tester")
    assert p.status is PersonaStatus.coming_soon
    # The dev prompt builder falls back to generic when modifier is empty;
    # if a coming_soon entry accidentally grows a modifier, the dev agent
    # would start following a half-baked role — guard against that.
    assert p.system_prompt_modifier == ""


def test_get_persona_unknown_falls_back_to_default():
    from pravi.personas import DEFAULT_PERSONA, get_persona

    assert get_persona("not_a_real_slug") is DEFAULT_PERSONA
    # `other` is the default — escape hatch for the generic prompt.
    assert DEFAULT_PERSONA.slug == "other"


def test_get_persona_none_or_empty_falls_back():
    from pravi.personas import DEFAULT_PERSONA, get_persona

    assert get_persona(None) is DEFAULT_PERSONA
    assert get_persona("") is DEFAULT_PERSONA


def test_catalog_has_exactly_six_active_personas():
    """ADR 0004 starter set. Promoting a coming_soon → active is a real
    decision; this test makes it impossible to do silently."""
    from pravi.personas import ACTIVE_PERSONAS

    slugs = {p.slug for p in ACTIVE_PERSONAS}
    assert slugs == {"architect", "frontend", "backend", "tester", "tech_writer", "other"}


def test_catalog_has_all_15_personas():
    """The full roadmap is part of the contract — coming_soon entries
    show in the UI as a roadmap signal."""
    from pravi.personas import ALL_PERSONAS

    assert len(ALL_PERSONAS) == 19  # 6 active + 13 coming_soon (see ADR 0004 catalog)


def test_all_personas_have_unique_slugs():
    """A duplicate slug would shadow earlier entries in the lookup map
    and quietly corrupt the picker."""
    from pravi.personas import ALL_PERSONAS

    slugs = [p.slug for p in ALL_PERSONAS]
    assert len(slugs) == len(set(slugs)), f"duplicate slugs in catalog: {slugs}"

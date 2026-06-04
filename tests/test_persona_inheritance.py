"""Persona + stack inheritance at ticket-create time — see ADR 0004.

The rule: explicit request value wins, otherwise inherit from the parent,
otherwise None (= catalog default at runtime).

These tests target the pure helper extracted from `create_ticket`. The
DB-level "did the row land with the right values?" test belongs to the
Tier-2 materializer test once those are added.
"""
from __future__ import annotations


def test_inherits_persona_and_stack_from_parent_when_request_null():
    from pravi.api.routes import _resolve_persona_stack

    persona, stack = _resolve_persona_stack(
        req_persona=None,
        req_stack=None,
        parent_persona="backend",
        parent_stack="python-fastapi",
    )
    assert persona == "backend"
    assert stack == "python-fastapi"


def test_explicit_persona_overrides_parent_independently_of_stack():
    """Both fields are independent — overriding persona doesn't drop the
    inherited stack."""
    from pravi.api.routes import _resolve_persona_stack

    persona, stack = _resolve_persona_stack(
        req_persona="frontend",
        req_stack=None,
        parent_persona="backend",
        parent_stack="python-fastapi",
    )
    assert persona == "frontend"
    assert stack == "python-fastapi"


def test_explicit_stack_overrides_parent_independently_of_persona():
    from pravi.api.routes import _resolve_persona_stack

    persona, stack = _resolve_persona_stack(
        req_persona=None,
        req_stack="typescript-react",
        parent_persona="backend",
        parent_stack="python-fastapi",
    )
    assert persona == "backend"
    assert stack == "typescript-react"


def test_top_level_no_parent_yields_none():
    """No parent, no explicit values — both end up None (catalog default
    takes over at runtime)."""
    from pravi.api.routes import _resolve_persona_stack

    assert _resolve_persona_stack(
        req_persona=None, req_stack=None,
        parent_persona=None, parent_stack=None,
    ) == (None, None)


def test_empty_string_request_clears_inheritance():
    """Form-sent `""` should clear, not inherit. Otherwise the user can
    never un-set persona on a child once a parent has one."""
    from pravi.api.routes import _resolve_persona_stack

    assert _resolve_persona_stack(
        req_persona="",
        req_stack="",
        parent_persona="backend",
        parent_stack="python-fastapi",
    ) == (None, None)


def test_explicit_request_with_no_parent():
    from pravi.api.routes import _resolve_persona_stack

    persona, stack = _resolve_persona_stack(
        req_persona="tester",
        req_stack="rust",
        parent_persona=None,
        parent_stack=None,
    )
    assert persona == "tester"
    assert stack == "rust"

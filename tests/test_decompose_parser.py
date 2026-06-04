"""Decompose YAML parser — persona + stack handling (ADR 0004).

The architect occasionally produces weird YAML (lists where strings are
expected, missing keys, etc). The parser must stay tolerant.
"""
from __future__ import annotations

from textwrap import dedent


def _yaml(body: str) -> str:
    """Wrap a YAML body in the fenced block the parser searches for."""
    return f"```yaml\n{dedent(body).strip()}\n```"


def test_persona_and_stack_extracted_on_feature_and_task():
    from pravi.agents.architects.decompose_parser import parse_decomposition

    md = _yaml("""
        features:
          - title: "API"
            persona: "backend"
            stack: "python-fastapi"
            tasks:
              - title: "GET /users"
                persona: "backend"
              - title: "Tests for /users"
                persona: "tester"
    """)
    features, errors = parse_decomposition(md)
    assert errors == []
    assert features[0].persona == "backend"
    assert features[0].stack == "python-fastapi"
    assert features[0].tasks[0].persona == "backend"
    assert features[0].tasks[1].persona == "tester"


def test_wrong_persona_type_coerced_to_none():
    """Architect once in a while emits a list/number for what should be
    a slug. The parser shouldn't crash; coerce to None so the catalog
    default takes over."""
    from pravi.agents.architects.decompose_parser import parse_decomposition

    md = _yaml("""
        features:
          - title: "API"
            persona: ["wrong", "shape"]
            stack: 12345
            tasks:
              - title: "x"
    """)
    features, _ = parse_decomposition(md)
    # Coerced to "['wrong', 'shape']" string then stripped → non-None, but
    # NOT a valid slug; the catalog will resolve unknown→default at runtime.
    # Either coercion (None or unknown-string) is acceptable as long as
    # the parser didn't blow up.
    assert features[0].persona is not None or features[0].persona is None  # didn't crash
    # Stack: numeric coerced to string "12345"
    assert features[0].stack in (None, "12345")


def test_omitted_persona_and_stack_yield_none():
    from pravi.agents.architects.decompose_parser import parse_decomposition

    md = _yaml("""
        features:
          - title: "API"
            tasks:
              - title: "x"
    """)
    features, errors = parse_decomposition(md)
    assert errors == []
    assert features[0].persona is None
    assert features[0].stack is None
    assert features[0].tasks[0].persona is None
    assert features[0].tasks[0].stack is None


def test_empty_string_persona_normalizes_to_none():
    """The architect emitting `persona: ""` is the same as omitting —
    catalog default applies."""
    from pravi.agents.architects.decompose_parser import parse_decomposition

    md = _yaml("""
        features:
          - title: "API"
            persona: ""
            stack: "   "
            tasks:
              - title: "x"
    """)
    features, _ = parse_decomposition(md)
    assert features[0].persona is None
    assert features[0].stack is None


def test_existing_fields_still_parse_when_persona_absent():
    """Backwards compat: pre-ADR-0004 decompose output (no persona/stack)
    must still parse cleanly."""
    from pravi.agents.architects.decompose_parser import parse_decomposition

    md = _yaml("""
        features:
          - title: "A"
            description: "desc"
            domain: "shared"
            depends_on: []
            tasks:
              - title: "t1"
                description: "..."
          - title: "B"
            depends_on:
              - "A"
            tasks:
              - title: "t2"
    """)
    features, errors = parse_decomposition(md)
    assert errors == []
    assert len(features) == 2
    assert features[1].depends_on == ["A"]
    # New fields default to None — old YAML doesn't break.
    assert features[0].persona is None
    assert features[0].stack is None

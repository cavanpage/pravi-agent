"""Clarify YAML parser — multi-choice options handling (ADR 0004 §
multi-choice).

The radio-buttons UI hinges on the parser correctly reading the
`options:` list. Tolerate omitted, empty, and badly-typed values.
"""
from __future__ import annotations

from textwrap import dedent


def _yaml(body: str) -> str:
    return f"```yaml\n{dedent(body).strip()}\n```"


def test_options_list_parsed():
    from pravi.agents.architects.clarify_parser import parse_clarifications

    md = _yaml("""
        questions:
          - text: "Should we drop v1?"
            why: "Determines whether to keep compat shims."
            options:
              - "Yes — drop now"
              - "Keep one release"
              - "Deprecate, drop later"
    """)
    qs, errors = parse_clarifications(md)
    assert errors == []
    assert qs[0].options == [
        "Yes — drop now",
        "Keep one release",
        "Deprecate, drop later",
    ]


def test_options_absent_means_open_ended():
    """Most questions don't have a meaningful preset list. The picker
    falls back to a free-text textarea when `options` is missing."""
    from pravi.agents.architects.clarify_parser import parse_clarifications

    md = _yaml("""
        questions:
          - text: "Anything that comes to mind?"
            why: "Capture open feedback."
    """)
    qs, errors = parse_clarifications(md)
    assert errors == []
    assert qs[0].options == []


def test_options_wrong_type_records_error_but_does_not_crash():
    """Architect emits `options: "yes/no"` (string instead of list).
    Parser records an error, returns empty options, keeps the rest of the
    question intact."""
    from pravi.agents.architects.clarify_parser import parse_clarifications

    md = _yaml("""
        questions:
          - text: "Question A"
            why: "..."
            options: "this is not a list"
    """)
    qs, errors = parse_clarifications(md)
    assert qs[0].text == "Question A"
    assert qs[0].options == []
    # Surfaces the issue so the UI can flag it.
    assert any("options" in e and "list" in e for e in errors)


def test_options_non_string_entries_coerced():
    """YAML coerces `yes`/`no`/numbers to Python booleans/ints. Coerce
    each option to a string so the UI labels are always renderable."""
    from pravi.agents.architects.clarify_parser import parse_clarifications

    md = _yaml("""
        questions:
          - text: "Pick one"
            why: "..."
            options:
              - yes
              - no
              - 42
    """)
    qs, errors = parse_clarifications(md)
    assert errors == []
    # Whatever the coercion (True → "True" / "yes" / etc), the entries
    # must be non-empty strings. Order preserved.
    assert len(qs[0].options) == 3
    assert all(isinstance(o, str) and o for o in qs[0].options)


def test_empty_questions_list_valid():
    """Architect deciding 'nothing worth asking' is the same as no
    questions — UI proceeds straight to decompose."""
    from pravi.agents.architects.clarify_parser import parse_clarifications

    md = _yaml("""
        questions: []
    """)
    qs, errors = parse_clarifications(md)
    assert qs == []
    assert errors == []

"""Acceptance-criteria rendering + extraction (ADR 0007).

Pure unit — no DB, no network. The extractor is the feature's back-compat
gate: a body with no criteria section must yield `[]`, which is what turns
the whole deploy + e2e leg off for every pre-0007 ticket.
"""

from __future__ import annotations

import pytest

from pravi.specs.acceptance import (
    extract_acceptance_criteria,
    has_acceptance_criteria,
    render_acceptance_section,
    render_task_body,
)


def test_absent_section_yields_nothing():
    assert extract_acceptance_criteria("Just a plain description.") == []
    assert extract_acceptance_criteria("") == []
    assert extract_acceptance_criteria(None) == []
    assert has_acceptance_criteria("## Tests\n\n- run pytest") is False


def test_extracts_checkbox_items():
    md = """\
Build the todo list.

## Acceptance criteria

- [ ] Visiting `/` shows a heading "Today's tasks".
- [x] Clicking "Add" with an empty input shows an inline error.
"""
    assert extract_acceptance_criteria(md) == [
        'Visiting `/` shows a heading "Today\'s tasks".',
        'Clicking "Add" with an empty input shows an inline error.',
    ]


@pytest.mark.parametrize(
    "heading",
    [
        "## Acceptance criteria",
        "### Acceptance Criteria",
        "#### acceptance criteria:",
        "## ACCEPTANCE CRITERIA:",
    ],
)
def test_heading_level_case_and_colon_variance(heading: str):
    """The heading is LLM-authored — exact-match would be brittle."""
    assert extract_acceptance_criteria(f"{heading}\n\n- One thing\n") == ["One thing"]


@pytest.mark.parametrize(
    "terminator",
    ["## Tests", "# Risks", "---", "***"],
)
def test_section_ends_at_next_heading_or_rule(terminator: str):
    md = f"## Acceptance criteria\n\n- Kept\n\n{terminator}\n\n- Not kept\n"
    assert extract_acceptance_criteria(md) == ["Kept"]


def test_section_runs_to_eof_when_nothing_follows():
    assert extract_acceptance_criteria("## Acceptance criteria\n\n- Only one") == [
        "Only one"
    ]


@pytest.mark.parametrize(
    "bullet",
    ["- item", "* item", "+ item", "1. item", "2) item", "- [ ] item", "  - item"],
)
def test_bullet_styles(bullet: str):
    assert extract_acceptance_criteria(f"## Acceptance criteria\n\n{bullet}") == ["item"]


def test_none_marker_is_not_a_criterion():
    """The architect writes this when a change has no user-visible surface;
    it must read as 'no criteria', not as a criterion named '(none…)'."""
    md = "## Acceptance criteria\n\n- _(none — not user-visible)_\n"
    assert extract_acceptance_criteria(md) == []
    assert has_acceptance_criteria(md) is False


def test_empty_and_punctuation_only_items_are_dropped():
    md = "## Acceptance criteria\n\n- \n- ---\n- Real one\n- __\n"
    assert extract_acceptance_criteria(md) == ["Real one"]


def test_prose_inside_the_section_is_ignored():
    md = """\
## Acceptance criteria

These are checked against the deployed preview:

- Visiting / works
"""
    assert extract_acceptance_criteria(md) == ["Visiting / works"]


def test_first_section_wins():
    md = (
        "## Acceptance criteria\n\n- First\n\n"
        "## Notes\n\n## Acceptance criteria\n\n- Second\n"
    )
    assert extract_acceptance_criteria(md) == ["First"]


def test_extraction_is_capped():
    md = "## Acceptance criteria\n\n" + "\n".join(f"- c{i}" for i in range(20))
    assert len(extract_acceptance_criteria(md)) == 8


def test_render_section_empty_input():
    assert render_acceptance_section([]) == ""
    assert render_acceptance_section(None) == ""
    assert render_acceptance_section(["  ", ""]) == ""


def test_render_task_body_round_trips_through_the_extractor():
    """The whole design rests on this: criteria written into the body at
    decompose time must come back out at run time."""
    criteria = ["Visiting / shows a heading", 'Clicking "Add" errors on empty input']
    body = render_task_body("Build the list.", criteria)
    assert body.startswith("Build the list.")
    assert extract_acceptance_criteria(body) == criteria


def test_render_task_body_without_criteria_is_just_the_description():
    assert render_task_body("Refactor the store.", []) == "Refactor the store."
    assert extract_acceptance_criteria(render_task_body("Refactor.", [])) == []


def test_render_task_body_handles_missing_description():
    body = render_task_body(None, ["Visiting / works"])
    assert extract_acceptance_criteria(body) == ["Visiting / works"]

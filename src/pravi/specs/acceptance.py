"""Acceptance criteria: the user-observable statements that become
Playwright tests (ADR 0007).

Criteria have no column of their own. They're rendered as a markdown
section inside the ticket body, which `synthesize_plan_from_body` copies
verbatim into `Plan.content_md` and `_build_dev_task` inlines into the dev
agent's prompt — so writing them into the body carries them the whole way
for free.

The extractor is the feature's back-compat gate: every ticket written
before this ADR lacks the heading, so it returns `[]`, and the workflow
takes exactly its pre-0007 path. No migration, no backfill, no per-ticket
flag.

Everything here is pure — no I/O, no DB — so it is safe to call from
workflow code as well as activities.
"""

from __future__ import annotations

import re

ACCEPTANCE_HEADING = "## Acceptance criteria"

# Marker the architect emits when a change has no user-visible surface.
# Extracted as an ordinary bullet would be, then filtered out below.
NONE_MARKER = "_(none — not user-visible)_"

# Tolerant of heading level (## vs ###), case, and a trailing colon —
# the text is LLM-authored, so exact-match would be brittle.
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*acceptance\s+criteria\s*:?\s*$", re.IGNORECASE)
# Any other heading ends the section.
_ANY_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")
_RULE_RE = re.compile(r"^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$")
# `- item`, `* item`, `+ item`, `1. item`, each optionally checkboxed.
_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s*)?(.+?)\s*$")

# Anything that is only punctuation/emphasis once stripped isn't a criterion.
_EMPTYISH_RE = re.compile(r"^[\s_*`~.—–-]*$")

MAX_CRITERIA = 8


def _is_none_marker(text: str) -> bool:
    stripped = text.strip().strip("_*`").strip()
    return stripped.lower().startswith("(none") or stripped.lower() == "none"


def extract_acceptance_criteria(markdown: str | None) -> list[str]:
    """Pull the bullet items out of the first "Acceptance criteria" section.

    The section runs from its heading to the next heading of any level, a
    horizontal rule, or EOF. Returns `[]` when the section is absent, empty,
    or explicitly marked as not user-visible — which is how the e2e leg
    no-ops on legacy tickets.
    """
    if not markdown:
        return []

    lines = markdown.splitlines()
    start: int | None = None
    for i, line in enumerate(lines):
        if _HEADING_RE.match(line):
            start = i + 1
            break
    if start is None:
        return []

    criteria: list[str] = []
    for line in lines[start:]:
        if _ANY_HEADING_RE.match(line) or _RULE_RE.match(line):
            break
        m = _ITEM_RE.match(line)
        if not m:
            continue
        text = m.group(1).strip()
        if _EMPTYISH_RE.match(text) or _is_none_marker(text):
            continue
        criteria.append(text)
        if len(criteria) >= MAX_CRITERIA:
            break
    return criteria


def has_acceptance_criteria(markdown: str | None) -> bool:
    return bool(extract_acceptance_criteria(markdown))


def render_acceptance_section(criteria: list[str] | None) -> str:
    """Markdown block for a ticket body. Empty input → empty string.

    Checkboxes rather than plain bullets: they read as a to-do list in the
    web UI's markdown renderer *and* in the GitHub PR body, where a human
    can tick them off during review.
    """
    items = [c.strip() for c in (criteria or []) if c and c.strip()]
    if not items:
        return ""
    bullets = "\n".join(f"- [ ] {c}" for c in items[:MAX_CRITERIA])
    return f"{ACCEPTANCE_HEADING}\n\n{bullets}"


def render_task_body(description: str | None, criteria: list[str] | None) -> str:
    """Compose a ticket body from the architect's description plus criteria."""
    body = (description or "").strip()
    section = render_acceptance_section(criteria)
    if not section:
        return body
    if not body:
        return section
    return f"{body}\n\n{section}"

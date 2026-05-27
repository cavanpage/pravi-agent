"""Parse the architect's ```yaml block from a clarify response.

Same tolerance contract as the decompose parser: on any failure the caller
keeps `raw_md` so the UI can show it for editing.
"""
from __future__ import annotations

import re

import yaml

from pravi.agents.protocols import ClarificationQuestion

_FENCED_YAML = re.compile(r"```ya?ml\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)


def parse_clarifications(raw_md: str) -> tuple[list[ClarificationQuestion], list[str]]:
    """Return (questions, errors). Empty questions list with no errors means
    the architect chose not to ask anything — proceed straight to decompose."""
    if not raw_md or not raw_md.strip():
        return [], ["empty response"]

    match = _FENCED_YAML.search(raw_md)
    if not match:
        return [], ["no ```yaml block found in architect output"]

    yaml_text = match.group(1)
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        return [], [f"YAML parse error: {e}"]

    if not isinstance(data, dict):
        return [], ["YAML root must be a mapping with a `questions` key"]
    raw = data.get("questions")
    # An empty list is a valid answer — the architect can say "nothing to ask".
    if raw is None or raw == []:
        return [], []
    if not isinstance(raw, list):
        return [], ["`questions` must be a list (or omitted/empty)"]

    questions: list[ClarificationQuestion] = []
    errors: list[str] = []
    for i, q in enumerate(raw):
        if not isinstance(q, dict):
            errors.append(f"questions[{i}] must be a mapping")
            continue
        text = (q.get("text") or "").strip()
        if not text:
            errors.append(f"questions[{i}].text is required")
            continue
        why = str(q.get("why") or "").strip()
        # Optional preset answer list. Stringify each entry so non-string
        # YAML values (yes/no, numbers) become safe labels.
        opts_raw = q.get("options") or []
        options: list[str] = []
        if isinstance(opts_raw, list):
            for o in opts_raw:
                s = str(o).strip()
                if s:
                    options.append(s)
        else:
            errors.append(f"questions[{i}].options must be a list when present")
        questions.append(
            ClarificationQuestion(text=text, why=why, options=options)
        )
    return questions, errors

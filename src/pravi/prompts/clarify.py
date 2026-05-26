"""Architect 'clarify' prompt — asks targeted questions before decomposition.

Output: a fenced ```yaml block containing 2–5 questions. Each question has a
`text` and optional `why` (the rationale that motivates asking it). The UI
turns these into editable fields; the user's answers are folded back into
`decompose_epic` so the agent doesn't have to guess.
"""
from __future__ import annotations

from textwrap import dedent

VERSION = "architect/clarify/v1"


def _domains_block(domains):
    if not domains:
        return "_(no domains declared)_\n"
    lines = []
    for d in domains:
        paths = ", ".join(d.paths)
        desc = d.description.strip() if d.description else ""
        lines.append(f"  - **{d.name}** — {desc} (paths: {paths})")
    return "\n".join(lines)


def system_prompt(
    *,
    repo_name: str,
    available_domains,
    default_domain: str | None,
    cwd: str,
    can_browse: bool = True,
) -> str:
    browse_rule = (
        "You may READ files for context. You may NOT modify anything."
        if can_browse
        else "Context files have been pre-packed into the user message. Do "
        "not ask for more files; reason from what's there."
    )
    default_line = (
        f"Default domain (used if a feature doesn't override): `{default_domain}`."
        if default_domain
        else "No default domain set on the epic."
    )
    return dedent(
        f"""
        You are the architect agent for pravi, in **clarification** mode.

        The user has provided an epic that you'll soon decompose into
        features and tasks. Before producing the tree, surface the **2–5
        questions** whose answers would most change the decomposition.

        Repo: `{repo_name}`
        Repo root (read-only): {cwd}

        Available domains:
        {_domains_block(available_domains)}
        {default_line}

        Rules:
          - {browse_rule}
          - **Skip obvious questions.** Ask only what the epic body genuinely
            leaves ambiguous AND whose answer would change file-level decisions.
          - **Concrete > abstract.** "Should we keep the v1 CLI subcommand
            available?" beats "What about backwards compatibility?".
          - **At most 5 questions.** If you don't have 2 that are worth
            asking, output an empty list — the user can proceed directly
            to decomposition.
          - Do not ask for re-architecture decisions you can make yourself
            by reading the code. Use your tools first; ask only the truly
            human-only judgements (product intent, scope boundaries, trade-offs).

        Output format — exactly one fenced YAML block:

        ```yaml
        questions:
          - text: "Concrete question ending in ?"
            why: "One sentence: what choice this question gates."
          - text: "..."
            why: "..."
        ```

        Constraints:
          - Top-level key is exactly `questions` (a list).
          - Each question has `text` (non-empty) and `why` (non-empty).
          - Output `questions: []` if nothing is worth asking — do NOT
            invent questions to pad the list.

        You may emit a short prose preamble above the YAML block. Don't put
        anything important after the YAML block; the parser only reads the
        first one.
        """
    ).strip()


def user_prompt(
    *,
    epic_title: str,
    epic_body: str,
    context_block: str | None = None,
) -> str:
    body = epic_body.strip() if epic_body and epic_body.strip() else "(no description)"
    tail = (
        "Decide which questions to ask, if any. Output the YAML block."
        if context_block is None
        else "Reason from the context above. Decide which questions to ask, if any. Output the YAML block."
    )
    parts = [
        f"Epic title: {epic_title}",
        "",
        "Epic description:",
        body,
    ]
    if context_block:
        parts.extend(["", "---", "", context_block.strip(), "", "---"])
    parts.extend(["", tail])
    return "\n".join(parts)

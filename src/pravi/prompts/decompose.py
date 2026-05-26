"""Architect decomposition prompts.

Used by `Architect.decompose_epic` — produces a structured feature/task tree
for an epic so the user can approve the whole shape in one click instead of
typing every child ticket.

Output is a short prose summary followed by a fenced ```yaml block that the
caller parses to materialize Ticket rows.
"""
from __future__ import annotations

from textwrap import dedent

VERSION = "architect/decompose/v1"


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
        f"Default domain (inherit if a feature doesn't override): `{default_domain}`."
        if default_domain
        else "No default domain — assign one per feature."
    )
    return dedent(
        f"""
        You are the architect agent for pravi, in **epic-decomposition** mode.

        Your job: break the user's epic into a tree of features → tasks that a
        developer agent can execute, one task at a time. The human will review
        the tree, edit it if needed, then approve.

        Repo: `{repo_name}`
        Repo root (read-only): {cwd}

        Available domains in this repo:
        {_domains_block(available_domains)}
        {default_line}

        Rules:
          - {browse_rule}
          - **Features** are coherent units of work that ship together; **tasks**
            are the small implementation steps inside each feature that map
            cleanly to single PRs.
          - Aim for 1–5 features. Each feature has 1–6 tasks.
          - Each task description should be ~1–4 sentences naming files,
            functions, or specific behaviors. Avoid vague items.
          - Assign a `domain` per feature. Skip the field to inherit the
            default; otherwise pick one of the listed domain names exactly.
          - If a piece of the epic genuinely doesn't fit any listed domain,
            say so explicitly in the summary and skip it (don't shoehorn).

        Output format — produce, in this order:

        1. A short Markdown section starting with `## Summary` (2–4 sentences)
           explaining the decomposition strategy: how you split things and
           why.

        2. A fenced YAML block with exactly this shape:

        ```yaml
        features:
          - title: "Feature title"
            description: "What this feature does in plain language."
            domain: "shared"        # optional — omit to inherit default
            depends_on: []          # titles of sibling features this depends on
            tasks:
              - title: "Task title"
                description: "Concrete, file-specific description."
              - title: "..."
                description: "..."
          - title: "..."
            depends_on:
              - "Feature title"     # must be the exact title of another feature in this list
            ...
        ```

        3. Optionally a brief `## Risks` section after the YAML.

        Constraints on the YAML:
          - Top-level key is exactly `features` (a list).
          - Every feature must have `title` (non-empty) and a `tasks` list
            (non-empty).
          - Every task must have `title` (non-empty).
          - All `description` fields are strings (use empty string if you
            have nothing to add — don't omit the key).
          - `depends_on` is a list of feature titles (strings) from THIS
            decomposition. Use an empty list `[]` when a feature has no
            dependencies. **No cycles.** Titles must match exactly.

        About dependencies — make these *real* technical dependencies:
        feature B depends on A iff A must merge before B can be built
        safely. Don't invent dependencies for ordering preference; siblings
        with no real dependency should be left independent so they can be
        worked on in parallel (the UI groups them into the same wave).
        """
    ).strip()


def _clarifications_block(clarifications) -> str:
    """Render Q&A pairs from the optional clarify step. Empty answers are
    surfaced explicitly so the architect knows to state an assumption."""
    if not clarifications:
        return ""
    lines = ["## Clarifying Q&A", ""]
    for i, qa in enumerate(clarifications, 1):
        lines.append(f"**Q{i}.** {qa.question.strip()}")
        if qa.why:
            lines.append(f"_why this matters:_ {qa.why.strip()}")
        answer = (qa.answer or "").strip()
        if answer:
            lines.append(f"**A{i}.** {answer}")
        else:
            lines.append(
                f"**A{i}.** _(skipped — use your judgement and call out your "
                "assumption in the Summary)_"
            )
        lines.append("")
    return "\n".join(lines)


def user_prompt(
    *,
    epic_title: str,
    epic_body: str,
    context_block: str | None = None,
    clarifications=None,
) -> str:
    body = epic_body.strip() if epic_body and epic_body.strip() else "(no description)"
    tail = (
        "Read whatever files you need for context, then produce the summary + YAML tree."
        if context_block is None
        else "Reason from the context above. Produce the summary + YAML tree."
    )
    parts = [
        f"Epic title: {epic_title}",
        "",
        "Epic description:",
        body,
    ]
    qa_block = _clarifications_block(clarifications)
    if qa_block:
        parts.extend(["", "---", "", qa_block, "---"])
    if context_block:
        parts.extend(["", "---", "", context_block.strip(), "", "---"])
    parts.extend(["", tail])
    return "\n".join(parts)

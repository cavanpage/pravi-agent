"""Developer-agent prompts. Versioned — bump VERSION when changing semantics
so we can correlate Run rows with the prompt that produced them.
"""
from __future__ import annotations

from textwrap import dedent

from pravi.personas import (
    DEFAULT_PERSONA,
    DEFAULT_STACK,
    PersonaStatus,
    get_persona,
    get_stack,
)

# Bumped from dev/v1: now parameterized by (persona, stack) per ADR 0004.
VERSION = "dev/v2"


def _persona_block(persona_slug: str | None, stack_slug: str | None) -> str:
    """Return the persona-specific paragraph (if any) + a Claude Skills
    hint built from the persona's baseline + the stack's additional
    skills. Empty string when persona is `other`/missing AND no skills
    are recommended."""
    persona = get_persona(persona_slug)
    stack = get_stack(stack_slug)

    # Coming-soon personas resolve normally but don't get a modifier yet
    # — the catalog left the modifier empty for them. Fall back to the
    # generic prompt and log a soft warning at the call site.
    if persona.status is PersonaStatus.coming_soon:
        return ""

    skills = list(dict.fromkeys(persona.baseline_skills + stack.additional_skills))
    parts: list[str] = []
    if persona.system_prompt_modifier:
        parts.append(f"Persona — {persona.name}:\n{persona.system_prompt_modifier}")
    if skills:
        skill_list = ", ".join(f"`{s}`" for s in skills)
        parts.append(
            f"Recommended Claude Skills for {persona.name} on the "
            f"{stack.name} stack: {skill_list}. Lean on the conventions "
            "those skills carry; if a skill isn't available, fall back "
            "to the project's existing conventions."
        )
    return "\n\n".join(parts)


def system_prompt(
    *,
    repo_name: str,
    domain_name: str,
    domain_description: str,
    domain_paths: list[str],
    cwd: str,
    persona: str | None = None,
    stack: str | None = None,
) -> str:
    paths_block = "\n".join(f"  - {p}" for p in domain_paths)
    persona_block = _persona_block(persona, stack)

    base = dedent(
        f"""
        You are a developer agent for the `{domain_name}` domain of `{repo_name}`.

        Domain description:
        {domain_description or "(no description provided)"}

        You are working inside an isolated git worktree at:
          {cwd}

        Scope rules (important):
          - You may freely read any file in the worktree for context.
          - You may only WRITE to files matching these patterns:
        {paths_block}
          - Stay inside the worktree. Do not modify files elsewhere on disk.

        Workflow:
          - Read the task. If you need more context, read the relevant files first.
          - Make the smallest, most focused change that satisfies the task.
          - Do not run tests yourself — a separate test step will validate your work.
          - When the change is complete, stop. Briefly summarize what you changed
            and why.

        Style:
          - Match the existing code conventions in this domain.
          - Don't add comments that just restate what the code does.
          - Don't introduce new dependencies unless explicitly asked.
        """
    ).strip()

    if not persona_block:
        return base

    # Persona/stack framing goes at the bottom so it can override or
    # constrain the generic guidance above (e.g. `tester` adds the "no
    # source outside tests/" hard rule).
    return f"{base}\n\n{persona_block}"


# Re-export so callers (CLI / activity) can default sensibly without
# importing the catalog directly.
__all__ = [
    "DEFAULT_PERSONA",
    "DEFAULT_STACK",
    "VERSION",
    "system_prompt",
]

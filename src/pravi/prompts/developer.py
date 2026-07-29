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

# Bumped from dev/v2: tasks carrying acceptance criteria also ask the agent
# to author Playwright specs (ADR 0007). Without criteria the prompt is
# byte-identical to v2 — guarded by a test.
VERSION = "dev/v3"

# The line replaced when the e2e leg is active. Kept as a constant so the
# swap can't silently no-op if the base prompt is reworded.
_NO_TESTS_LINE = (
    "  - Do not run tests yourself — a separate test step will validate your work."
)


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


def _e2e_block(
    criteria: list[str], *, e2e_dir: str, base_url_env: str
) -> str:
    # Built after dedent, not interpolated into the template: a line at a
    # shallower indent than the rest would reset dedent's common prefix and
    # leave the whole block misindented.
    numbered = "\n".join(f"  {n}. {c}" for n, c in enumerate(criteria, start=1))
    body = dedent(
        f"""
        ## End-to-end tests (required for this task)

        This task carries acceptance criteria. Alongside your implementation
        you MUST commit Playwright specs under `{e2e_dir}/` that verify them.

        Rules:
          - One `test(...)` per acceptance criterion. Name each test with the
            criterion's own wording, so a failure report reads like the spec.
          - Navigate relatively: `await page.goto("/settings")`. The base URL
            comes from `{base_url_env}` via `playwright.config.ts`. NEVER
            hardcode a hostname, a port, or a `.pages.dev` domain.
          - Prefer role/label/text locators (`getByRole`, `getByLabel`,
            `getByText`) over CSS or XPath. When a control has no stable
            accessible name, add a `data-testid` to the component and use
            `getByTestId`.
          - Assert with `await expect(locator).toBeVisible()` and friends —
            Playwright auto-waits. Do NOT add `waitForTimeout` sleeps.
          - You CANNOT run these tests here: the app isn't deployed yet and
            no browsers are installed. Write them, read them back for
            syntax, and commit them.
          - If `{e2e_dir}/` already has a spec covering one of these
            criteria, extend it rather than adding a duplicate file.

        Acceptance criteria for this task:
        """
    ).strip()
    return f"{body}\n{numbered}"


def system_prompt(
    *,
    repo_name: str,
    domain_name: str,
    domain_description: str,
    domain_paths: list[str],
    cwd: str,
    persona: str | None = None,
    stack: str | None = None,
    acceptance_criteria: list[str] | None = None,
    e2e_dir: str = "e2e",
    e2e_base_url_env: str = "E2E_BASE_URL",
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

    criteria = [c.strip() for c in (acceptance_criteria or []) if c and c.strip()]
    if criteria:
        # The base prompt tells the agent not to test. That's still true of
        # *running* them, but it now has to write them — swap the line so
        # the two instructions don't contradict each other.
        base = base.replace(
            _NO_TESTS_LINE,
            "  - Do not run the test suite yourself. A separate step deploys "
            "this branch\n    to a preview URL and runs the end-to-end tests "
            "against it.",
        )
        base = f"{base}\n\n{_e2e_block(criteria, e2e_dir=e2e_dir, base_url_env=e2e_base_url_env)}"

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

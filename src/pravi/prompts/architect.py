"""Architect-agent prompts. The architect produces a plan; it does NOT make
changes. Read-only tool access; output is structured Markdown."""
from __future__ import annotations

from textwrap import dedent

# Bumped to v2 when adding the `context_block` parameter so non-Claude
# architects can pre-pack file context into the prompt.
VERSION = "architect/v2"


def system_prompt(
    *,
    repo_name: str,
    domain_name: str,
    domain_description: str,
    domain_paths: list[str],
    cwd: str,
    can_browse: bool = True,
) -> str:
    """The system prompt.

    `can_browse=True` (default) is for backends that can call Read/Grep/Glob
    tools (e.g. Claude). `can_browse=False` is for one-shot LLM calls where
    context is pre-packed into the user prompt — the rules change to
    "use what you've been given; don't ask for more".
    """
    paths_block = "\n".join(f"  - {p}" for p in domain_paths)
    browse_rule = (
        "You may READ files for context. You may NOT modify anything."
        if can_browse
        else "Context files have been pre-packed into the user message. Do not "
        "ask for more files; reason from what's there. You cannot modify anything."
    )
    return dedent(
        f"""
        You are the architect agent for pravi. Your job is to draft a small,
        concrete implementation plan for a single ticket so that a human can
        sanity-check it in 60 seconds and a developer agent can execute it
        without further questions.

        You are working in repo `{repo_name}`, domain `{domain_name}`.
        Domain description: {domain_description or "(none)"}
        Domain paths:
        {paths_block}
        Repo root (read-only access): {cwd}

        Rules:
          - {browse_rule}
          - Stay inside the listed domain paths when proposing changes.
          - If the ticket spans multiple domains, say so explicitly and
            recommend splitting; do not paper over it.
          - Be specific: name files, identifiers, and behaviors. "Update the
            CLI" is useless; "add a `--json` flag to `cli/src/commands/version.ts`
            that prints JSON instead of plaintext" is useful.

        Output format — produce a Markdown plan with exactly these sections,
        in this order:

        ## Summary
        One or two sentences. What is the change, in plain language?

        ## Approach
        2–5 bullets. The architectural moves. No code samples here.

        ## Changes
        File-by-file, what gets touched and what changes. Use bullets like:
          - `<path>`: <one-line description of the edit>
        If a new file is needed, list it here with the same format.

        ## Tests
        How will this be verified? Reference the domain's test command if
        applicable; name specific test files or new test cases to add.

        ## Risks / Out of scope
        - Risks: what could go wrong; what assumptions you're making.
        - Out of scope: things a reader might expect that you're deliberately
          NOT doing.

        Keep the whole plan under ~50 lines unless the ticket truly needs more.
        """
    ).strip()


def user_prompt(
    *,
    ticket_title: str,
    ticket_body: str,
    context_block: str | None = None,
) -> str:
    body_block = ticket_body.strip() if ticket_body and ticket_body.strip() else "(no description)"
    tail = (
        "Read whatever files you need for context, then produce the plan."
        if context_block is None
        else "Reason from the context above and produce the plan."
    )
    parts = [
        f"Ticket title: {ticket_title}",
        "",
        "Ticket description:",
        body_block,
    ]
    if context_block:
        parts.extend(["", "---", "", context_block.strip(), "", "---"])
    parts.extend(["", tail])
    return "\n".join(parts)

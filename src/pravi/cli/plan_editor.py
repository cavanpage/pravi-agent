"""$EDITOR-based plan editing loop. Stays inside the CLI process so it's
human-interactive — never call this from inside a Temporal activity.

Plan files persist under ~/.pravi/plans/<slug>.md so the user can re-open
them across sessions if needed."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from rich.console import Console
from rich.prompt import Prompt

console = Console()


class PlanDecision(StrEnum):
    approve = "approve"
    cancel = "cancel"


@dataclass
class EditedPlan:
    decision: Literal["approve", "cancel"]
    content: str
    path: Path


PLAN_DIR = Path.home() / ".pravi" / "plans"


def _editor() -> list[str]:
    """Resolve the user's editor.

    `EDITOR=vi -O` is valid (multi-arg), so we split on whitespace rather
    than pass as a single token. Defaults to `vi` if unset."""
    raw = os.environ.get("EDITOR", "vi")
    return raw.split()


def _open_editor(path: Path) -> None:
    cmd = [*_editor(), str(path)]
    # Inherit stdio so the editor takes over the terminal cleanly.
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        console.print(f"[yellow]editor exited non-zero ({result.returncode}); content preserved[/]")


def edit_plan(slug: str, draft: str) -> EditedPlan:
    """Open the draft in $EDITOR, then prompt the user to approve/revise/cancel.

    Returns the approved content (if approved) or raises a clean cancel.
    The file at `path` is left on disk so the user can re-open later.
    """
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    path = PLAN_DIR / f"{slug}.md"
    path.write_text(draft, encoding="utf-8")

    console.print(f"[dim]opening {path} in {' '.join(_editor())}...[/]")
    while True:
        _open_editor(path)
        content = path.read_text(encoding="utf-8")

        if not content.strip():
            console.print("[red]plan is empty[/]")
            choice = Prompt.ask(
                "[a]bort, [r]e-edit?", choices=["a", "r"], default="r"
            )
            if choice == "a":
                return EditedPlan(decision="cancel", content="", path=path)
            continue

        # Show a quick fingerprint so the user knows what they're approving.
        line_count = sum(1 for _ in content.splitlines())
        console.print(f"[green]saved[/] {path}  ({line_count} lines, {len(content)} chars)")
        choice = Prompt.ask(
            "approve plan? [a]pprove / [r]e-edit / [c]ancel",
            choices=["a", "r", "c"],
            default="a",
        )
        if choice == "a":
            return EditedPlan(decision="approve", content=content, path=path)
        if choice == "c":
            return EditedPlan(decision="cancel", content=content, path=path)
        # else: re-edit loop

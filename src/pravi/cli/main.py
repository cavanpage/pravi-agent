from __future__ import annotations

import typer

from pravi.cli import ticket as ticket_cli
from pravi.cli.dev import dev as dev_cmd
from pravi.cli.lifecycle import plan as plan_cmd
from pravi.cli.lifecycle import ticket_start
from pravi.cli.web import web as web_cmd

app = typer.Typer(
    name="pravi",
    help="Agentic feature builder for domain-driven repos.",
    no_args_is_help=True,
)

app.add_typer(ticket_cli.app, name="ticket")
ticket_cli.app.command(
    "start",
    help="Persist a ticket and start the FeatureWorkflow (blocks on plan signal).",
)(ticket_start)
app.command(name="dev", help="Run the developer agent against a task in a worktree.")(
    dev_cmd
)
app.command(name="plan", help="Draft a plan, edit in $EDITOR, approve, signal workflow.")(
    plan_cmd
)
app.command(name="web", help="Start the pravi web API for the plan-review UI.")(web_cmd)


@app.callback()
def _root() -> None:
    """pravi — plan, develop, review, and ship features with Claude."""


if __name__ == "__main__":
    app()

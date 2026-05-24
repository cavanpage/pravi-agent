from __future__ import annotations

import typer

from pravi.cli import ticket as ticket_cli

app = typer.Typer(
    name="pravi",
    help="Agentic feature builder for domain-driven repos.",
    no_args_is_help=True,
)

app.add_typer(ticket_cli.app, name="ticket")


@app.callback()
def _root() -> None:
    """pravi — plan, develop, review, and ship features with Claude."""


if __name__ == "__main__":
    app()

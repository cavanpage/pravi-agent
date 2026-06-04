"""`pravi openapi-dump` — write the FastAPI app's OpenAPI spec to disk.

Importing `pravi.api.app` boots the app via `create_app()` (the module-level
`app = create_app()`), which loads settings and configures logging. We re-use
the already-constructed `app` so the dumped schema matches what the running
server would serve.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from pravi.api.app import create_app

console = Console()

# Repo-root-relative default. src/pravi/cli/openapi_dump.py -> repo root is
# three parents up from src/pravi/cli/.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_OUT = _REPO_ROOT / "docs" / "api" / "openapi.json"


def openapi_dump(
    out: Annotated[
        Path,
        typer.Option(
            "--out",
            "-o",
            help="Path to write the OpenAPI JSON to.",
        ),
    ] = _DEFAULT_OUT,
) -> None:
    """Dump the FastAPI OpenAPI schema to a pretty-printed JSON file."""
    app = create_app()
    schema = app.openapi()

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")

    console.print(f"[green]Wrote OpenAPI spec[/] [bold]{out}[/]")

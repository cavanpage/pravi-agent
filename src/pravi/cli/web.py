"""`pravi web` — boot the FastAPI server that backs the plan-review UI."""
from __future__ import annotations

from typing import Annotated

import typer
import uvicorn
from rich.console import Console

console = Console()


def web(
    host: Annotated[str, typer.Option(help="Host to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to bind.")] = 8765,
    reload: Annotated[
        bool,
        typer.Option("--reload", help="Auto-reload on source changes (dev only)."),
    ] = False,
) -> None:
    """Start the pravi web API (FastAPI + SSE).

    For active frontend development, also run the Vite dev server:

        cd web && npm install && npm run dev

    Vite serves the UI at http://localhost:5173 and proxies /api to this server.
    For production-style use, build the React app once (`cd web && npm run build`)
    and `pravi web` will serve it on the same port as the API.
    """
    console.print(f"[bold]pravi web[/]  http://{host}:{port}")
    console.print("[dim]API:[/]    http://{host}:{port}/api/...")
    console.print("[dim]Health:[/] http://{host}:{port}/healthz")
    console.print(
        "[dim]For hot reload during frontend dev:[/] cd web && npm run dev "
        "[dim](serves http://localhost:5173)[/]"
    )
    uvicorn.run(
        "pravi.api.app:app",
        host=host,
        port=port,
        reload=reload,
        log_config=None,
    )

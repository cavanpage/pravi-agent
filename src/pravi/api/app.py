"""FastAPI app factory."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pravi.api.auth_routes import router as auth_router
from pravi.api.cloudflare_routes import router as cloudflare_router
from pravi.api.routes import router
from pravi.config import apply_anthropic_auth, get_settings
from pravi.logging_setup import configure_logging

WEB_DIST = Path(__file__).resolve().parent.parent.parent.parent / "web" / "dist"


def create_app() -> FastAPI:
    # Configure structlog at import time so every route's `log.info(...)`
    # actually reaches stdout. Without this, the worker + CLIs print logs
    # but the FastAPI process is silent — confusing for "why isn't anything
    # happening" debugging.
    configure_logging(get_settings().log_level)
    # If ANTHROPIC_API_KEY is in .env, push it into os.environ so the SDK
    # finds it. No-op when only the shell env or the `claude login` session
    # is the auth source. Logs the active mode once.
    apply_anthropic_auth()
    app = FastAPI(title="pravi", version="0.1.0")

    # CORS — for dev when Vite runs on a different port than FastAPI.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router)
    app.include_router(auth_router)
    app.include_router(cloudflare_router)

    # In production: serve the built React app from FastAPI. In dev, Vite
    # runs separately; this just means the index page is unavailable until
    # you `npm run build` once.
    if WEB_DIST.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=WEB_DIST / "assets"),
            name="assets",
        )

        @app.get("/{path:path}")
        async def spa_fallback(path: str) -> FileResponse:
            # SPA fallback — any non-API GET returns index.html so client-side
            # routing works.
            return FileResponse(WEB_DIST / "index.html")

    return app


app = create_app()

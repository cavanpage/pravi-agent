"""Starter templates committed to brand-new repos created via pravi.

Each template exposes a `FILES: dict[str, str]` mapping of relative path
→ file content. The create-repo flow seeds these into a fresh repo as
the initial commit. Templates are deliberately tiny — they're a
launching point, not a framework. The dev agent fills out everything
else.

`v1` ships with one template (Vite + React + TS + Tailwind). More can
land later (Cloudflare Workers API, FastAPI backend, etc.) by following
the same shape.
"""

from pravi.templates import vite_react_static

# Slug → module mapping for the API to pick from.
ALL_TEMPLATES: dict[str, dict[str, str]] = {
    "vite-react-static": vite_react_static.FILES,
}

__all__ = ["ALL_TEMPLATES"]

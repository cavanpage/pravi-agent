"""Starter templates committed to brand-new repos created via pravi.

Each template module exposes `render(*, project_name, repo_full_name)
-> TemplateManifest` — the rendered file tree plus the build/deploy
config the create-repo flow needs (ADR 0006 slice 1). Templates are
deliberately tiny — they're a launching point, not a framework; the dev
agent fills out everything else.

Adding a template: write a sibling module with a `render()` factory and
register it in `ALL_TEMPLATES` below. The manifest unit tests in
`tests/test_templates.py` cover every registered slug automatically.
"""

from collections.abc import Callable

from pravi.templates import llm_chat, vite_react_static
from pravi.templates.manifest import DeploySpec, TemplateManifest

# A factory takes (project_name=..., repo_full_name=...) keyword args.
TemplateFactory = Callable[..., TemplateManifest]

# Slug → manifest factory for the API to pick from.
ALL_TEMPLATES: dict[str, TemplateFactory] = {
    "vite-react-static": vite_react_static.render,
    "llm-chat": llm_chat.render,
}

__all__ = ["ALL_TEMPLATES", "DeploySpec", "TemplateFactory", "TemplateManifest"]

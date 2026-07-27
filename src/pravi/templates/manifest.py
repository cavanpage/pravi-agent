"""Template manifest — what a starter template ships and how it deploys.

Each template module exposes `render(*, project_name, repo_full_name)
-> TemplateManifest`. The create-repo flow pushes `manifest.files` as the
initial commit and reads the build/deploy fields instead of hardcoding
them per template (ADR 0006 slice 1).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DeploySpec:
    """Where/how this template deploys.

    Deliberately minimal — the DeployTarget Protocol is deferred to
    ADR 0006 slice 2; grow this only when a second target lands.
    """

    pages: bool = True
    # Workers AI binding name the template's committed wrangler.toml
    # declares (e.g. "AI"), or None. Informational in slice 1: the
    # binding config ships in the repo, not via the Pages API — this
    # field drives the templates-endpoint deploy hint and is the hook
    # for an API-side deployment_configs fallback if git builds ever
    # stop honoring wrangler.toml.
    ai_binding: str | None = None


@dataclass(frozen=True)
class TemplateManifest:
    slug: str
    title: str  # picker display name, e.g. "Vite + React + Tailwind"
    description: str  # one-liner for the picker card
    build_command: str  # e.g. "npm run build"
    destination_dir: str  # e.g. "dist"
    files: dict[str, str]  # rendered relative path -> content
    deploy: DeploySpec = field(default_factory=DeploySpec)


def substitute(content: str, *, project_name: str, repo_full_name: str) -> str:
    """Substitute the per-repo placeholders into a template file body."""
    return content.replace("%PROJECT_NAME%", project_name).replace(
        "%REPO_FULL_NAME%", repo_full_name
    )


def render_files(raw: dict[str, str], *, project_name: str, repo_full_name: str) -> dict[str, str]:
    """Apply placeholder substitution across a raw file map."""
    return {
        path: substitute(content, project_name=project_name, repo_full_name=repo_full_name)
        for path, content in raw.items()
    }

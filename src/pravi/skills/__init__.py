"""Preset agent skills pravi can grant to dev runs.

Skills are real claude-agent-sdk Agent Skills (SKILL.md packages), shipped
as a **local plugin** under `plugin/` in this package. Loading them via the
SDK's `plugins` option keeps `setting_sources=[]` — the dev agent stays
hermetic (no host or target-repo settings leak in) while still getting the
skill content.

A target repo opts in per skill via a top-level `skills:` list in its
`.builder/domains.yaml`; templates pre-fill theirs. Slugs are validated
against `AVAILABLE_SKILLS` at load time so a typo fails fast instead of
silently granting nothing.
"""

from __future__ import annotations

from pathlib import Path

# The plugin root the SDK loads (parent of `skills/`).
PLUGIN_NAME = "pravi"
PLUGIN_PATH = Path(__file__).parent / "plugin"


def available_skills() -> list[str]:
    """Slugs of every skill the plugin ships (directory names)."""
    skills_dir = PLUGIN_PATH / "skills"
    if not skills_dir.is_dir():
        return []
    return sorted(p.parent.name for p in skills_dir.glob("*/SKILL.md") if p.is_file())


def qualified(slugs: list[str]) -> list[str]:
    """Map bare slugs to the SDK's plugin-namespaced skill names."""
    return [f"{PLUGIN_NAME}:{s}" for s in slugs]


__all__ = ["PLUGIN_NAME", "PLUGIN_PATH", "available_skills", "qualified"]

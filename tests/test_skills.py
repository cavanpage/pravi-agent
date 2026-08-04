"""Skills catalog + domains-yaml wiring (pure unit)."""

from __future__ import annotations

import pytest
import yaml

from pravi.domains.registry import DomainsFile
from pravi.skills import PLUGIN_PATH, available_skills, qualified

MINIMAL = """
domains:
  - name: app
    paths: ["src/**"]
"""


def test_catalog_ships_expected_skills():
    slugs = available_skills()
    assert "cloudflare-deploy" in slugs
    assert "workers-ai" in slugs
    # Every skill is a real SKILL.md with frontmatter the SDK can load.
    for slug in slugs:
        body = (PLUGIN_PATH / "skills" / slug / "SKILL.md").read_text()
        assert body.startswith("---"), f"{slug} missing frontmatter"
        assert f"name: {slug}" in body


def test_plugin_manifest_exists():
    assert (PLUGIN_PATH / ".claude-plugin" / "plugin.json").is_file()


def test_qualified_namespacing():
    assert qualified(["workers-ai"]) == ["pravi:workers-ai"]


def test_domains_file_accepts_known_skills():
    raw = yaml.safe_load(MINIMAL + "skills:\n  - cloudflare-deploy\n")
    f = DomainsFile.model_validate(raw)
    assert f.skills == ["cloudflare-deploy"]


def test_domains_file_defaults_to_no_skills():
    f = DomainsFile.model_validate(yaml.safe_load(MINIMAL))
    assert f.skills == []


def test_domains_file_rejects_unknown_skill():
    raw = yaml.safe_load(MINIMAL + "skills:\n  - kubernetes-deploy\n")
    with pytest.raises(Exception, match="unknown skills"):
        DomainsFile.model_validate(raw)

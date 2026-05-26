from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reset_settings_singleton(monkeypatch):
    """`config.get_settings()` memoizes. Reset between tests so each one
    sees the env it set up."""
    from pravi import config

    monkeypatch.setattr(config, "_settings", None)
    yield
    monkeypatch.setattr(config, "_settings", None)


def test_get_architect_defaults_to_claude(monkeypatch):
    monkeypatch.delenv("PRAVI_ARCHITECT_PROVIDER", raising=False)
    monkeypatch.delenv("PRAVI_ARCHITECT_MODEL", raising=False)
    from pravi.agents.architects.claude import ClaudeArchitect
    from pravi.agents.factory import get_architect

    arch = get_architect()
    assert isinstance(arch, ClaudeArchitect)


def test_get_architect_litellm(monkeypatch):
    monkeypatch.setenv("PRAVI_ARCHITECT_PROVIDER", "litellm")
    monkeypatch.setenv("PRAVI_ARCHITECT_MODEL", "gpt-5")
    from pravi.agents.architects.litellm import LiteLLMArchitect
    from pravi.agents.factory import get_architect

    arch = get_architect()
    assert isinstance(arch, LiteLLMArchitect)
    assert arch.model == "gpt-5"


def test_get_architect_unknown_provider_raises(monkeypatch):
    # Pydantic Literal validator rejects this when Settings instantiates.
    monkeypatch.setenv("PRAVI_ARCHITECT_PROVIDER", "bogus")
    from pravi.config import Settings

    with pytest.raises(Exception):  # ValidationError from pydantic-settings
        Settings()


def test_get_dev_agent_returns_claude(monkeypatch):
    monkeypatch.delenv("PRAVI_DEV_PROVIDER", raising=False)
    from pravi.agents.dev.claude import ClaudeDevAgent
    from pravi.agents.factory import get_dev_agent

    dev = get_dev_agent()
    assert isinstance(dev, ClaudeDevAgent)


def test_context_builder_includes_listed_files(tmp_path: Path):
    """`build_context` should embed files named in `context_files` and a tree
    of files matching the domain paths."""
    from pravi.agents.architects.context import build_context

    # Set up a minimal git repo so `git ls-files` returns something.
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=tmp_path, check=True
    )

    pkg = tmp_path / "packages" / "shared"
    pkg.mkdir(parents=True)
    (pkg / "README.md").write_text("# shared\n\nThe shared package.")
    (pkg / "index.ts").write_text("export const x = 1;")
    (tmp_path / "packages" / "other").mkdir()
    (tmp_path / "packages" / "other" / "ignored.ts").write_text("not in domain")

    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)

    packed = build_context(
        tmp_path,
        domain_paths=["packages/shared/**"],
        context_files=["packages/shared/README.md"],
    )

    # The README content shows up.
    assert "# shared" in packed.text
    assert "packages/shared/README.md" in packed.files_included
    # The tree includes the shared files...
    assert "packages/shared/index.ts" in packed.text
    # ...but not the other package's files.
    assert "packages/other/ignored.ts" not in packed.text


def test_context_builder_truncates_at_byte_budget(tmp_path: Path):
    """A huge file should be trimmed and the result flagged truncated."""
    from pravi.agents.architects.context import build_context

    (tmp_path / "big.md").write_text("x" * 200_000)
    packed = build_context(
        tmp_path,
        domain_paths=[],
        context_files=["big.md"],
        max_bytes=1000,
    )
    assert packed.truncated
    assert len(packed.text) < 5000  # well below the original 200KB


def test_context_builder_rejects_path_escape(tmp_path: Path):
    """`../` traversal in context_files must not leak files outside the repo."""
    from pravi.agents.architects.context import build_context

    outside = tmp_path.parent / "secret.txt"
    outside.write_text("don't include me")

    packed = build_context(
        tmp_path,
        domain_paths=[],
        context_files=["../secret.txt"],
    )
    assert "don't include me" not in packed.text
    assert "../secret.txt" not in packed.files_included

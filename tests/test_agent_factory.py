from __future__ import annotations

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


def test_get_architect_per_mode_models(monkeypatch):
    """Cheap-model architecting happens within Claude via per-mode pins."""
    monkeypatch.setenv("PRAVI_ARCHITECT_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("PRAVI_ARCHITECT_CLARIFY_MODEL", "claude-haiku-4-5-20251001")
    from pravi.agents.factory import get_architect

    arch = get_architect()
    assert arch.model == "claude-sonnet-5"
    assert arch.clarify_model == "claude-haiku-4-5-20251001"
    # Unpinned modes fall back to the default model.
    assert arch.decompose_model == "claude-sonnet-5"


@pytest.mark.parametrize("provider", ["litellm", "bogus"])
def test_get_architect_non_claude_provider_rejected(monkeypatch, provider):
    """The LiteLLM architect was dropped (ADR 0002 amendment) — any
    non-claude provider now fails Settings validation."""
    monkeypatch.setenv("PRAVI_ARCHITECT_PROVIDER", provider)
    from pydantic import ValidationError

    from pravi.config import Settings

    with pytest.raises(ValidationError):
        Settings()


def test_get_dev_agent_returns_claude(monkeypatch):
    monkeypatch.delenv("PRAVI_DEV_PROVIDER", raising=False)
    from pravi.agents.dev.claude import ClaudeDevAgent
    from pravi.agents.factory import get_dev_agent

    dev = get_dev_agent()
    assert isinstance(dev, ClaudeDevAgent)

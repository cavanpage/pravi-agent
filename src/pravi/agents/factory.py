"""Provider dispatch — translates `PRAVI_*_PROVIDER` settings into an impl."""

from __future__ import annotations

from pravi.agents.protocols import Architect, DevAgent
from pravi.config import get_settings


def get_architect() -> Architect:
    s = get_settings()
    if s.architect_provider == "claude":
        from pravi.agents.architects.claude import ClaudeArchitect

        return ClaudeArchitect(
            model=s.architect_model,
            clarify_model=s.architect_clarify_model,
            decompose_model=s.architect_decompose_model,
            draft_model=s.architect_draft_model,
        )
    raise ValueError(
        f"unknown architect provider {s.architect_provider!r}; only 'claude' is implemented"
    )


def get_dev_agent() -> DevAgent:
    s = get_settings()
    if s.dev_provider == "claude":
        from pravi.agents.dev.claude import ClaudeDevAgent

        return ClaudeDevAgent(model=s.dev_model)
    raise ValueError(f"unknown dev provider {s.dev_provider!r}; only 'claude' is implemented")

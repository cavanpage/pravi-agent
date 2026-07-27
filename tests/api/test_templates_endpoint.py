"""Tests for GET /api/auth/github/templates and create-repo template
validation. Endpoint functions are called directly — no HTTP server."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from pravi.api.auth_routes import create_new_repo, list_templates
from pravi.api.schemas import CreateRepoRequest
from pravi.templates import ALL_TEMPLATES


async def test_list_templates_covers_registry():
    out = await list_templates()
    assert {t.slug for t in out} == set(ALL_TEMPLATES)
    for t in out:
        assert t.title and t.description and t.deploy_hint


async def test_llm_chat_hint_mentions_workers_ai():
    out = await list_templates()
    hint = next(t.deploy_hint for t in out if t.slug == "llm-chat")
    assert "Workers AI" in hint


async def test_create_repo_unknown_template_is_400(monkeypatch):
    """The registry-membership check must reject before anything is
    created on GitHub."""

    class _Conn:
        access_token = "tok"
        github_user_login = "alice"

    async def _fake_conn():
        return _Conn()

    from pravi.api import auth_routes

    monkeypatch.setattr(auth_routes.gh, "get_active_connection", _fake_conn)
    with pytest.raises(HTTPException) as exc:
        await create_new_repo(CreateRepoRequest(name="x", template="nope"))
    assert exc.value.status_code == 400
    assert "unknown template" in exc.value.detail

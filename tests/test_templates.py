"""Manifest-shape tests for every registered starter template.

Pure unit — no network, no DB. Anything registered in `ALL_TEMPLATES`
gets the generic checks automatically; per-template specifics follow.
"""

from __future__ import annotations

import pytest

from pravi.templates import ALL_TEMPLATES

PROJECT = "my-app"
REPO = "alice/my-app"

UNIVERSAL_FILES = ("README.md", ".gitignore", ".builder/domains.yaml", "package.json")


def _render(slug: str):
    return ALL_TEMPLATES[slug](project_name=PROJECT, repo_full_name=REPO)


@pytest.mark.parametrize("slug", sorted(ALL_TEMPLATES))
def test_manifest_shape(slug: str):
    m = _render(slug)
    assert m.slug == slug
    assert m.title and m.description
    assert m.build_command and m.destination_dir
    assert m.files


@pytest.mark.parametrize("slug", sorted(ALL_TEMPLATES))
def test_no_leftover_placeholders(slug: str):
    """A file body with a raw %TOKEN% means substitution missed it and
    the user's fresh repo would ship the placeholder verbatim."""
    m = _render(slug)
    for path, content in m.files.items():
        for token in ("%PROJECT_NAME%", "%REPO_FULL_NAME%", "%MODEL%"):
            assert token not in content, f"{slug}:{path} still contains {token}"


@pytest.mark.parametrize("slug", sorted(ALL_TEMPLATES))
def test_universal_files_and_substitution(slug: str):
    m = _render(slug)
    for path in UNIVERSAL_FILES:
        assert path in m.files, f"{slug} is missing {path}"
    assert PROJECT in m.files["package.json"]
    assert PROJECT in m.files["index.html"]


def test_vite_react_static_specifics():
    m = _render("vite-react-static")
    assert set(m.files) == {
        "package.json",
        "vite.config.ts",
        "tsconfig.json",
        "tsconfig.app.json",
        "index.html",
        "src/main.tsx",
        "src/App.tsx",
        "src/index.css",
        ".gitignore",
        ".builder/domains.yaml",
        "README.md",
    }
    assert m.destination_dir == "dist"
    assert m.deploy.pages is True
    assert m.deploy.ai_binding is None


def test_llm_chat_specifics():
    m = _render("llm-chat")
    assert "functions/api/chat.ts" in m.files
    assert "wrangler.toml" in m.files
    assert m.deploy.ai_binding == "AI"

    wrangler = m.files["wrangler.toml"]
    assert 'pages_build_output_dir = "dist"' in wrangler
    assert "[ai]" in wrangler
    assert 'binding = "AI"' in wrangler
    assert f'name = "{PROJECT}"' in wrangler

    chat_fn = m.files["functions/api/chat.ts"]
    assert "env.AI" in chat_fn
    assert "429" in chat_fn  # quota errors surface as HTTP 429
    assert "@cf/" in chat_fn  # a concrete Workers AI model slug is pinned

    app = m.files["src/App.tsx"]
    assert "/api/chat" in app
    assert "429" in app  # UI renders the quota banner

    # The Function must stay out of the app's tsc build — Cloudflare's
    # esbuild compiles it instead.
    assert '"include": ["src"]' in m.files["tsconfig.app.json"]
    # Two-domain manifest: UI + Function.
    domains = m.files[".builder/domains.yaml"]
    assert "name: frontend" in domains
    assert "name: api" in domains

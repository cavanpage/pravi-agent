"""Manifest-shape tests for every registered starter template.

Pure unit — no network, no DB. Anything registered in `ALL_TEMPLATES`
gets the generic checks automatically; per-template specifics follow.
"""

from __future__ import annotations

import json

import pytest
import yaml

from pravi.domains.registry import DomainsFile
from pravi.templates import ALL_TEMPLATES

PROJECT = "my-app"
REPO = "alice/my-app"

UNIVERSAL_FILES = (
    "README.md",
    ".gitignore",
    ".builder/domains.yaml",
    "package.json",
    "playwright.config.ts",
    "e2e/smoke.spec.ts",
)


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


@pytest.mark.parametrize("slug", sorted(ALL_TEMPLATES))
def test_domains_yaml_validates_and_declares_preview(slug: str):
    """Cross-check: every template's config must actually parse through
    the registry that reads it at run time, and must opt into the preview
    + e2e leg (ADR 0007). A template shipping a `preview:` block the
    registry rejects would silently disable the loop for that repo."""
    m = _render(slug)
    parsed = DomainsFile.model_validate(yaml.safe_load(m.files[".builder/domains.yaml"]))
    assert parsed.preview is not None, f"{slug} ships no preview: block"
    assert parsed.preview.provider == "cloudflare-pages"
    # The e2e dir the config names must be the one the specs live in.
    e2e_dir = parsed.preview.e2e.dir
    assert any(p.startswith(f"{e2e_dir}/") for p in m.files), (
        f"{slug} declares e2e.dir={e2e_dir!r} but ships no specs there"
    )
    assert "--reporter=json" in parsed.preview.e2e.command


@pytest.mark.parametrize("slug", sorted(ALL_TEMPLATES))
def test_playwright_scaffolding(slug: str):
    m = _render(slug)
    cfg = m.files["playwright.config.ts"]
    assert "E2E_BASE_URL" in cfg
    # The JSON reporter must write to stdout — pravi parses that stream.
    # An `outputFile:` key would redirect it to disk and the run would
    # look empty. (The word appears in a warning comment; the key must not.)
    assert "outputFile:" not in cfg
    assert 'reporter: process.env.CI ? "json"' in cfg

    pkg = json.loads(m.files["package.json"])
    assert "@playwright/test" in pkg["devDependencies"]
    assert pkg["scripts"]["e2e"] == "playwright test"

    # The smoke spec's anchor has to exist in the app it tests.
    assert 'data-testid="app-root"' in m.files["src/App.tsx"]
    assert 'getByTestId("app-root")' in m.files["e2e/smoke.spec.ts"]

    for artifact in ("test-results/", "playwright-report/"):
        assert artifact in m.files[".gitignore"]


@pytest.mark.parametrize("slug", sorted(ALL_TEMPLATES))
def test_e2e_specs_never_hardcode_a_host(slug: str):
    """Specs must navigate relatively so the same suite runs against a
    preview URL, production, and localhost."""
    for path, content in m_files(slug):
        if path.startswith("e2e/"):
            assert "http://" not in content, f"{slug}:{path} hardcodes a host"
            assert ".pages.dev" not in content, f"{slug}:{path} hardcodes a host"


def m_files(slug: str):
    return _render(slug).files.items()


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
        "playwright.config.ts",
        "e2e/smoke.spec.ts",
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
    # Three-domain manifest: UI + Function + e2e specs.
    domains = m.files[".builder/domains.yaml"]
    assert "name: frontend" in domains
    assert "name: api" in domains
    assert "name: e2e" in domains

    # The chat spec must tolerate a Workers AI free-tier 429 — a quota
    # error is an expected outcome, not a product bug. Asserting a reply
    # always arrives would burn the whole repair budget on something the
    # agent can't fix.
    chat_spec = m.files["e2e/chat.spec.ts"]
    assert ".or(" in chat_spec
    assert 'getByTestId("quota-banner")' in chat_spec
    # Every testid the specs select on has to exist in the component.
    for testid in ("chat-input", "chat-send", "quota-banner"):
        assert f'data-testid="{testid}"' in app, f"missing data-testid={testid}"
    # This one is set from the message role, so it's an expression.
    assert '"assistant-message"' in app

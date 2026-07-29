"""The optional `preview:` block in .builder/domains.yaml (ADR 0007).

Back-compat is the point of most of these: a repo whose domains.yaml
predates the feature must load unchanged and yield `preview is None`,
which turns the deploy + e2e leg off.
"""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from pravi.domains.registry import DomainRegistry, DomainsFile

_MINIMAL = """\
domains:
  - name: frontend
    paths: ["src/**"]
"""

_FULL = """\
domains:
  - name: frontend
    paths: ["src/**"]

preview:
  provider: cloudflare-pages
  project: my-app
  wait_timeout_seconds: 600
  first_deployment_grace_seconds: 90
  e2e:
    dir: tests/e2e
    install: ["pnpm", "install", "--frozen-lockfile"]
    browsers: ["chromium", "firefox"]
    command: ["pnpm", "exec", "playwright", "test", "--reporter=json"]
    base_url_env: BASE_URL
    timeout_seconds: 1200
"""


def _parse(text: str) -> DomainsFile:
    return DomainsFile.model_validate(yaml.safe_load(text))


def test_absent_preview_block_is_none():
    parsed = _parse(_MINIMAL)
    assert parsed.preview is None
    assert [d.name for d in parsed.domains] == ["frontend"]


def test_full_block_round_trips():
    p = _parse(_FULL).preview
    assert p is not None
    assert p.project == "my-app"
    assert p.wait_timeout_seconds == 600
    assert p.first_deployment_grace_seconds == 90
    assert p.e2e.dir == "tests/e2e"
    assert p.e2e.install == ["pnpm", "install", "--frozen-lockfile"]
    assert p.e2e.browsers == ["chromium", "firefox"]
    assert p.e2e.base_url_env == "BASE_URL"
    assert p.e2e.timeout_seconds == 1200


def test_defaults_fill_in_for_a_bare_block():
    p = _parse(_MINIMAL + "\npreview: {}\n").preview
    assert p is not None
    assert p.provider == "cloudflare-pages"
    assert p.project is None  # falls back to the Repo row / name probe
    assert p.e2e.dir == "e2e"
    assert p.e2e.install == ["npm", "ci"]
    assert p.e2e.browsers == ["chromium"]
    assert p.e2e.command == ["npx", "playwright", "test", "--reporter=json"]
    assert p.e2e.base_url_env == "E2E_BASE_URL"


def test_unknown_provider_is_rejected():
    with pytest.raises(ValidationError):
        _parse(_MINIMAL + "\npreview:\n  provider: vercel\n")


@pytest.mark.parametrize("bad_dir", ["/abs/e2e", "../outside", "a/../../b"])
def test_e2e_dir_must_stay_inside_the_repo(bad_dir: str):
    with pytest.raises(ValidationError):
        _parse(_MINIMAL + f"\npreview:\n  e2e:\n    dir: {bad_dir!r}\n")


def test_e2e_dir_is_normalized():
    p = _parse(_MINIMAL + "\npreview:\n  e2e:\n    dir: 'e2e/'\n").preview
    assert p is not None
    assert p.e2e.dir == "e2e"


@pytest.mark.parametrize(
    "field,value",
    [
        ("wait_timeout_seconds", 10),  # below the 60s floor
        ("wait_timeout_seconds", 99_999),  # above the 1h ceiling
        ("first_deployment_grace_seconds", 5),
    ],
)
def test_out_of_range_timeouts_are_rejected(field: str, value: int):
    with pytest.raises(ValidationError):
        _parse(_MINIMAL + f"\npreview:\n  {field}: {value}\n")


def test_registry_exposes_preview(tmp_path):
    """The property the workflow reads, over a real on-disk config."""
    cfg = tmp_path / ".builder"
    cfg.mkdir()
    (cfg / "domains.yaml").write_text(_FULL)
    reg = DomainRegistry.load(tmp_path)
    assert reg.preview is not None
    assert reg.preview.project == "my-app"

    (cfg / "domains.yaml").write_text(_MINIMAL)
    assert DomainRegistry.load(tmp_path).preview is None

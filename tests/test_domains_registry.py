from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from pravi.domains.registry import DomainRegistry, DomainsFile


def _write(tmp_path: Path, body: str) -> Path:
    cfg = tmp_path / ".builder" / "domains.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(dedent(body).lstrip())
    return tmp_path


def test_loads_valid_file(tmp_path: Path) -> None:
    repo = _write(
        tmp_path,
        """
        domains:
          - name: cli
            paths: ["packages/cli/**"]
            test: "npm test"
          - name: shared
            paths: ["packages/shared/**"]
        """,
    )
    reg = DomainRegistry.load(repo)
    assert reg.names() == ["cli", "shared"]
    assert reg.get("cli").test == "npm test"


def test_rejects_duplicate_names(tmp_path: Path) -> None:
    repo = _write(
        tmp_path,
        """
        domains:
          - name: cli
            paths: ["a/**"]
          - name: cli
            paths: ["b/**"]
        """,
    )
    with pytest.raises(ValueError, match="duplicate"):
        DomainRegistry.load(repo)


def test_rejects_bad_name() -> None:
    with pytest.raises(ValueError, match="slug"):
        DomainsFile.model_validate(
            {"domains": [{"name": "has space", "paths": ["x/**"]}]},
        )


def test_missing_paths_rejected() -> None:
    with pytest.raises(ValueError):
        DomainsFile.model_validate({"domains": [{"name": "cli", "paths": []}]})


def test_override_file_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    override = tmp_path / "other.yaml"
    override.write_text(
        dedent(
            """
            domains:
              - name: cli
                paths: ["pkg/**"]
            """
        ).lstrip()
    )
    reg = DomainRegistry.load(repo, override_file=override)
    assert reg.names() == ["cli"]

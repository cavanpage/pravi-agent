from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator


class Domain(BaseModel):
    name: str
    paths: list[str] = Field(min_length=1)
    description: str = ""
    test: str | None = None
    build: str | None = None
    context_files: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name_is_slug(cls, v: str) -> str:
        if not v or not all(c.isalnum() or c in "-_" for c in v):
            raise ValueError(f"domain name must be a slug: {v!r}")
        return v


class DomainsFile(BaseModel):
    domains: list[Domain] = Field(min_length=1)

    @field_validator("domains")
    @classmethod
    def _unique_names(cls, v: list[Domain]) -> list[Domain]:
        seen: set[str] = set()
        for d in v:
            if d.name in seen:
                raise ValueError(f"duplicate domain name: {d.name}")
            seen.add(d.name)
        return v


class DomainRegistry:
    """Loads + validates `.builder/domains.yaml` for a target repo."""

    CONFIG_PATH = Path(".builder/domains.yaml")

    def __init__(self, repo_root: Path, file: DomainsFile) -> None:
        self.repo_root = repo_root
        self.file = file

    @classmethod
    def load(cls, repo_root: Path, override_file: Path | None = None) -> DomainRegistry:
        repo_root = repo_root.expanduser().resolve()
        cfg = override_file.expanduser().resolve() if override_file else repo_root / cls.CONFIG_PATH
        if not cfg.is_file():
            raise FileNotFoundError(f"missing {cfg}; pravi requires a domains.yaml")
        raw = yaml.safe_load(cfg.read_text())
        return cls(repo_root, DomainsFile.model_validate(raw))

    @property
    def domains(self) -> list[Domain]:
        return self.file.domains

    def get(self, name: str) -> Domain:
        for d in self.domains:
            if d.name == name:
                return d
        raise KeyError(f"no domain named {name!r} in {self.repo_root}/{self.CONFIG_PATH}")

    def names(self) -> list[str]:
        return [d.name for d in self.domains]

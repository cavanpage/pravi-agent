from __future__ import annotations

from pathlib import Path
from typing import Literal

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


class E2EConfig(BaseModel):
    """How to run the end-to-end suite against a deployed preview.

    Commands are argv lists, not shell strings — they're fed straight to
    the sandbox's `exec`, which never invokes a shell.
    """

    dir: str = "e2e"
    install: list[str] = Field(default_factory=lambda: ["npm", "ci"])
    browsers: list[str] = Field(default_factory=lambda: ["chromium"])
    command: list[str] = Field(
        default_factory=lambda: ["npx", "playwright", "test", "--reporter=json"]
    )
    base_url_env: str = "E2E_BASE_URL"
    timeout_seconds: int = Field(default=900, ge=30, le=3600)

    @field_validator("dir")
    @classmethod
    def _safe_rel_dir(cls, v: str) -> str:
        if v.startswith("/") or ".." in Path(v).parts:
            raise ValueError(f"e2e.dir must be a relative path inside the repo: {v!r}")
        return v.strip("/")


class PreviewConfig(BaseModel):
    """Opt-in: deploy each ticket's branch to an ephemeral preview and run
    the e2e suite against it (ADR 0007). Absent from domains.yaml means the
    whole leg is off, which is how every pre-0007 repo stays unaffected."""

    provider: Literal["cloudflare-pages"] = "cloudflare-pages"
    # Cloudflare Pages project name. None → fall back to the Repo row's
    # `cf_pages_project`, then to a one-shot name probe.
    project: str | None = None
    wait_timeout_seconds: int = Field(default=900, ge=60, le=3600)
    # How long to keep looking before concluding that Cloudflare never
    # registered a build for the pushed commit at all (webhook lag).
    first_deployment_grace_seconds: int = Field(default=120, ge=30, le=600)
    e2e: E2EConfig = Field(default_factory=E2EConfig)


class DomainsFile(BaseModel):
    domains: list[Domain] = Field(min_length=1)
    preview: PreviewConfig | None = None
    # Preset pravi skills granted to every dev run on this repo — how a
    # repo tells pravi about its deployment/platform reality (e.g.
    # "cloudflare-deploy", "workers-ai"). Validated against the catalog
    # in `pravi.skills` so a typo fails at load, not silently at run.
    skills: list[str] = Field(default_factory=list)

    @field_validator("domains")
    @classmethod
    def _unique_names(cls, v: list[Domain]) -> list[Domain]:
        seen: set[str] = set()
        for d in v:
            if d.name in seen:
                raise ValueError(f"duplicate domain name: {d.name}")
            seen.add(d.name)
        return v

    @field_validator("skills")
    @classmethod
    def _known_skills(cls, v: list[str]) -> list[str]:
        from pravi.skills import available_skills

        known = set(available_skills())
        unknown = [s for s in v if s not in known]
        if unknown:
            raise ValueError(f"unknown skills {unknown!r}; available: {sorted(known)}")
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

    @property
    def preview(self) -> PreviewConfig | None:
        """The repo's preview/e2e config, or None when the leg is off."""
        return self.file.preview

    def get(self, name: str) -> Domain:
        for d in self.domains:
            if d.name == name:
                return d
        raise KeyError(f"no domain named {name!r} in {self.repo_root}/{self.CONFIG_PATH}")

    def names(self) -> list[str]:
        return [d.name for d in self.domains]

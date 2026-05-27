import os
from pathlib import Path
from typing import Annotated, Literal

import structlog
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

log = structlog.get_logger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="PRAVI_",
        extra="ignore",
    )

    db_url: str = "postgresql+asyncpg://pravi:pravi@localhost:5433/pravi"
    temporal_host: str = "localhost:7233"
    temporal_namespace: str = "default"
    # Task queue split — see scripts/setup-temporal.sh and README for rationale.
    # `features` carries orchestration + cheap activities (git, github).
    # `llm`      carries token-burning activities; cap concurrency on the worker
    #            with --max-activities to bound spend.
    temporal_task_queue_features: str = "pravi-features"
    temporal_task_queue_llm: str = "pravi-llm"
    worktree_base: Path = Field(default=Path.home() / ".pravi" / "worktrees")
    # Where lazily-cloned GitHub repos land. Created on demand the first time
    # a ticket is created against a repo we haven't cloned yet (via the
    # "search GitHub" picker in the new-ticket form).
    clone_base: Path = Field(default=Path.home() / ".pravi" / "repos")
    # `NoDecode` tells pydantic-settings to skip JSON-decoding for this
    # field; the validator below parses the raw env string instead.
    target_repos: Annotated[list[Path], NoDecode] = Field(default_factory=list)

    @field_validator("target_repos", mode="before")
    @classmethod
    def _parse_target_repos(cls, v):
        """Accept the comma-separated string form .env.example documents.

        Pydantic-settings v2 requires JSON syntax (`["a", "b"]`) for env-
        derived list fields by default; that's an unfriendly contract for
        a value users hand-write in .env. We coerce a bare comma-separated
        string into a list here. Empty / whitespace-only → []. Anything
        already list-shaped passes through.
        """
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        return v
    log_level: str = "INFO"

    # Architect-agent provider + budgets.
    architect_provider: Literal["claude", "litellm"] = "claude"
    # `architect_model` is the default for every architect mode. Each mode
    # (clarify, decompose, draft) has its own optional override below — set
    # one to pin a specific model for that mode and leave the others on the
    # default. Use this to put e.g. Sonnet on clarify (cheap question-
    # asking) while keeping Opus on decompose/draft where reasoning quality
    # cascades into every downstream task.
    architect_model: str | None = None
    # Clarify defaults to Haiku 4.5 — the questions step is cheap reasoning
    # and the latency is what users feel most acutely (it gates the rest of
    # the decompose flow). Override via PRAVI_ARCHITECT_CLARIFY_MODEL.
    architect_clarify_model: str | None = "claude-haiku-4-5-20251001"
    architect_decompose_model: str | None = None
    architect_draft_model: str | None = None
    architect_max_wall_seconds: int = 300
    architect_max_turns: int = 30
    architect_max_cost_usd: float = 1.0

    # Dev-agent provider + budgets.
    dev_provider: Literal["claude"] = "claude"  # only Claude implemented for now
    dev_model: str | None = None
    dev_max_wall_seconds: int = 1800  # Temporal-side timeout for a single dev run
    dev_max_turns: int = 50  # SDK-side cap on agent iterations
    dev_max_cost_usd: float = 5.0  # SDK-side hard budget per run

    # Default cumulative spend ceiling per ticket, in USD.
    # Null = unlimited. Used when neither the ticket nor any ancestor sets
    # `cost_ceiling_usd` explicitly. Pre-flight enforced before each dev run.
    ticket_cost_ceiling_usd: float | None = None

    # API key. Two ways to set it:
    #   1. Shell env: `export ANTHROPIC_API_KEY=sk-...`. The SDK picks it up
    #      automatically and pravi does nothing.
    #   2. In .env: `ANTHROPIC_API_KEY=sk-...` (or `PRAVI_ANTHROPIC_API_KEY=`).
    #      `apply_anthropic_auth()` reads it via the alias below and exports
    #      it to `os.environ` so the SDK can find it.
    # If unset, the SDK falls back to the local `claude login` session
    # (Claude.ai Pro/Max subscription quota). That's the default for owners
    # without a console.anthropic.com API account.
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ANTHROPIC_API_KEY", "PRAVI_ANTHROPIC_API_KEY"
        ),
    )

    # Web UI base URL (used by the CLI to print clickable links after starting
    # a ticket). Defaults to the FastAPI server's own port — change to
    # http://localhost:5173 if you typically run the Vite dev server.
    web_url_base: str = "http://localhost:8765"

    # ---- GitHub OAuth (for the in-UI "Connect GitHub" flow + PR creation) ----
    # Get these from https://github.com/settings/developers → New OAuth App.
    # Set Callback URL to http://localhost:8765/api/auth/github/callback
    # (or whatever matches github_oauth_redirect_uri below).
    github_oauth_client_id: str | None = None
    github_oauth_client_secret: str | None = None
    github_oauth_redirect_uri: str = (
        "http://localhost:8765/api/auth/github/callback"
    )
    # Scopes requested. `repo` is needed to push branches + open PRs on
    # private repos; for public-only use `public_repo`.
    github_oauth_scopes: str = "repo,read:user"
    # Where the user lands after the OAuth dance. Usually the home page.
    github_oauth_success_redirect: str = "http://localhost:8765/"

    @property
    def worktree_base_resolved(self) -> Path:
        return self.worktree_base.expanduser().resolve()

    @property
    def clone_base_resolved(self) -> Path:
        return self.clone_base.expanduser().resolve()


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


_auth_applied: bool = False


def apply_anthropic_auth() -> None:
    """Wire `settings.anthropic_api_key` (loaded from .env) into `os.environ`
    so claude-agent-sdk can find it.

    The SDK reads `ANTHROPIC_API_KEY` directly from the process environment
    at query time. Pydantic-settings loads .env values into Settings fields
    but does NOT push them back to `os.environ` — so without this step a
    key set in .env would be silently ignored.

    Idempotent. Logs once at startup so it's obvious which auth mode is
    active when diagnosing "is this burning Max quota or API dollars?".
    """
    global _auth_applied
    if _auth_applied:
        return
    settings = get_settings()
    if settings.anthropic_api_key:
        # Only set if we have a value AND the shell hasn't already set one.
        # Shell-set keys always win — that's the user's most explicit signal.
        if not os.environ.get("ANTHROPIC_API_KEY"):
            os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
            log.info("anthropic.auth", mode="api_key", source=".env")
        else:
            log.info("anthropic.auth", mode="api_key", source="shell_env")
    elif os.environ.get("ANTHROPIC_API_KEY"):
        log.info("anthropic.auth", mode="api_key", source="shell_env")
    else:
        # No API key anywhere — SDK falls back to claude login session.
        log.info(
            "anthropic.auth",
            mode="subscription",
            note="no ANTHROPIC_API_KEY found; SDK will use `claude login` session (Pro/Max)",
        )
    _auth_applied = True

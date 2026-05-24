from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="PRAVI_",
        extra="ignore",
    )

    db_url: str = "postgresql+asyncpg://pravi:pravi@localhost:5433/pravi"
    temporal_host: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "pravi-features"
    worktree_base: Path = Field(default=Path.home() / ".pravi" / "worktrees")
    target_repos: list[Path] = Field(default_factory=list)
    log_level: str = "INFO"

    @property
    def worktree_base_resolved(self) -> Path:
        return self.worktree_base.expanduser().resolve()


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings

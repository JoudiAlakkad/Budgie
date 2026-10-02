"""Settings read from environment variables (docs/wiki/backend/configuration.md)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Field names map to upper-case env vars."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "gemma3:4b"
    llm_api_key: str = "ollama"
    llm_timeout_s: float = 120
    llm_max_retries: int = 1
    llm_temperature: float = 0
    llm_max_tokens: int = 1024
    prompt_version: str = "v1"

    database_url: str = "sqlite:///./data/budgie.db"
    upload_dir: str = "./data/uploads"
    max_upload_mb: int = 10

    log_level: str = "INFO"
    frontend_dir: str = "./frontend"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings; also used as a FastAPI dependency."""
    return Settings()

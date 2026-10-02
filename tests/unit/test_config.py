"""Settings come from env vars with the defaults in docs/wiki/backend/configuration.md."""

import pytest

from app.config import Settings

DEFAULTS = {
    "llm_base_url": "http://localhost:11434/v1",
    "llm_model": "gemma3:4b",
    "llm_api_key": "ollama",
    "llm_timeout_s": 120,
    "llm_max_retries": 1,
    "llm_temperature": 0,
    "llm_max_tokens": 1024,
    "prompt_version": "v1",
    "database_url": "sqlite:///./data/budgie.db",
    "upload_dir": "./data/uploads",
    "max_upload_mb": 10,
    "log_level": "INFO",
    "frontend_dir": "./frontend",
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in DEFAULTS:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.mark.parametrize(("field", "expected"), DEFAULTS.items())
def test_default(field: str, expected: object) -> None:
    assert getattr(Settings(_env_file=None), field) == expected


@pytest.mark.parametrize(
    ("env", "value", "field", "expected"),
    [
        ("LLM_BASE_URL", "http://localhost:8080/v1", "llm_base_url", "http://localhost:8080/v1"),
        ("LLM_MODEL", "qwen2.5vl:7b", "llm_model", "qwen2.5vl:7b"),
        ("LLM_TIMEOUT_S", "30", "llm_timeout_s", 30),
        ("LLM_TEMPERATURE", "0.2", "llm_temperature", 0.2),
        ("MAX_UPLOAD_MB", "5", "max_upload_mb", 5),
        ("DATABASE_URL", "sqlite:////data/budgie.db", "database_url", "sqlite:////data/budgie.db"),
    ],
)
def test_env_override(
    monkeypatch: pytest.MonkeyPatch, env: str, value: str, field: str, expected: object
) -> None:
    monkeypatch.setenv(env, value)

    assert getattr(Settings(_env_file=None), field) == expected

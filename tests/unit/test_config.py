"""Settings come from env vars with the defaults in docs/wiki/backend/configuration.md."""

import pytest
from pydantic import SecretStr

from app.ai.client import ChatCompletion
from app.config import Settings
from app.errors import NotAReceipt
from app.services.dependencies import get_extractor_factory, get_llm_client

DEFAULTS = {
    "llm_base_url": "http://localhost:11434/v1",
    "llm_model": "gemma3:4b",
    "llm_api_key": "ollama",
    "llm_timeout_s": 180,
    "llm_max_retries": 1,
    "llm_temperature": 0,
    "llm_max_tokens": 2048,
    "prompt_version": "v1",
    "database_url": "sqlite:///./data/budgie.db",
    "upload_dir": "./data/uploads",
    "max_upload_mb": 10,
    "log_level": "INFO",
    "frontend_dir": "./frontend",
}


def plain(value: object) -> object:
    """Unwrap SecretStr so secrets compare like the other settings."""
    return value.get_secret_value() if isinstance(value, SecretStr) else value


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in DEFAULTS:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.mark.parametrize(("field", "expected"), DEFAULTS.items())
def test_default(field: str, expected: object) -> None:
    assert plain(getattr(Settings(_env_file=None), field)) == expected


OVERRIDES = [
    ("LLM_BASE_URL", "http://localhost:8080/v1", "llm_base_url", "http://localhost:8080/v1"),
    ("LLM_MODEL", "qwen2.5vl:7b", "llm_model", "qwen2.5vl:7b"),
    ("LLM_API_KEY", "sk-test-not-a-real-key", "llm_api_key", "sk-test-not-a-real-key"),
    ("LLM_TIMEOUT_S", "30", "llm_timeout_s", 30),
    ("LLM_MAX_RETRIES", "3", "llm_max_retries", 3),
    ("LLM_TEMPERATURE", "0.2", "llm_temperature", 0.2),
    ("LLM_MAX_TOKENS", "4096", "llm_max_tokens", 4096),
    ("PROMPT_VERSION", "v2", "prompt_version", "v2"),
    ("DATABASE_URL", "sqlite:////data/budgie.db", "database_url", "sqlite:////data/budgie.db"),
    ("UPLOAD_DIR", "/data/uploads", "upload_dir", "/data/uploads"),
    ("MAX_UPLOAD_MB", "5", "max_upload_mb", 5),
    ("LOG_LEVEL", "DEBUG", "log_level", "DEBUG"),
    ("FRONTEND_DIR", "/app/frontend", "frontend_dir", "/app/frontend"),
]


def test_overrides_cover_every_setting() -> None:
    assert {field for _, _, field, _ in OVERRIDES} == set(Settings.model_fields) == set(DEFAULTS)


@pytest.mark.parametrize(("env", "value", "field", "expected"), OVERRIDES)
def test_env_override(
    monkeypatch: pytest.MonkeyPatch, env: str, value: str, field: str, expected: object
) -> None:
    monkeypatch.setenv(env, value)

    assert plain(getattr(Settings(_env_file=None), field)) == expected


def test_api_key_is_not_in_repr_or_str(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test-not-a-real-key")
    settings = Settings(_env_file=None)

    assert "sk-test-not-a-real-key" not in repr(settings)
    assert "sk-test-not-a-real-key" not in str(settings)
    assert "sk-test-not-a-real-key" not in repr(settings.model_dump())


def test_llm_client_gets_the_plain_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test-not-a-real-key")

    assert get_llm_client(Settings(_env_file=None)).api_key == "sk-test-not-a-real-key"


def test_llm_client_gets_the_retry_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MAX_RETRIES", "3")

    assert get_llm_client(Settings(_env_file=None)).max_retries == 3


def test_extractor_uses_the_llm_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_TEMPERATURE", "0.2")
    monkeypatch.setenv("LLM_MAX_TOKENS", "4096")
    settings = Settings(_env_file=None)

    sent: list[dict] = []

    class RecordingClient:
        model = "gemma3:4b"

        def chat_completion(self, messages: list[dict], **kwargs: object) -> ChatCompletion:
            sent.append(kwargs)
            return ChatCompletion('{"is_receipt": false}', "stop", 1, 1, cut_off=False)

    extractor = get_extractor_factory(settings, RecordingClient())()  # type: ignore[arg-type]
    with pytest.raises(NotAReceipt):
        extractor.extract(b"img", "image/jpeg")

    assert (extractor.model, extractor.prompt_version) == ("gemma3:4b", "v1")
    assert (sent[0]["temperature"], sent[0]["max_tokens"]) == (0.2, 4096)

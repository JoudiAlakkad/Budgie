"""`GET /api/health` (contracts/api-endpoints.md): always 200, `{status, db, llm, model}`."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import StorageError
from app.main import create_app
from app.services.dependencies import dispose_databases, get_db_ping, get_llm_client
from tests.conftest import FakeLLMClient


def test_health_db_ok_llm_down(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "llm": "down", "model": "gemma3:4b"}


def test_health_llm_ok(client: TestClient, fake_llm: FakeLLMClient) -> None:
    fake_llm.up = True

    assert client.get("/api/health").json()["llm"] == "ok"


def test_health_db_error_still_200(client: TestClient) -> None:
    def broken_ping() -> None:
        raise StorageError("down")

    client.app.dependency_overrides[get_db_ping] = lambda: broken_ping
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["db"] == "error"


def test_model_comes_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_llm: FakeLLMClient
) -> None:
    """Swapping the model or server is a config change only (configuration.md)."""
    monkeypatch.setenv("LLM_MODEL", "llava:7b")
    monkeypatch.setenv("LLM_BASE_URL", "http://other-server:8080/v1")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'env.db'}")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    settings = Settings(_env_file=None)

    app = create_app(settings)
    app.dependency_overrides[get_llm_client] = lambda: fake_llm
    with TestClient(app) as client:
        body = client.get("/api/health").json()
    dispose_databases()

    assert settings.llm_base_url == "http://other-server:8080/v1"
    assert body["model"] == "llava:7b"


def test_starts_without_frontend_dir(client: TestClient) -> None:
    assert client.get("/api/health").status_code == 200
    assert client.get("/").status_code == 404


def test_serves_frontend_dir_but_api_wins(settings: Settings, tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<h1>Budgie</h1>", encoding="utf-8")

    app = create_app(settings.model_copy(update={"frontend_dir": str(frontend)}))
    app.dependency_overrides[get_llm_client] = lambda: FakeLLMClient()
    with TestClient(app) as client:
        index = client.get("/")
        health = client.get("/api/health")
    dispose_databases()

    assert index.status_code == 200
    assert "Budgie" in index.text
    assert health.json()["status"] == "ok"


def test_empty_frontend_dir_does_not_serve_cwd(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Path("") is the cwd; an empty FRONTEND_DIR must not expose it."""
    (tmp_path / "secret.txt").write_text("not public", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    app = create_app(settings.model_copy(update={"frontend_dir": ""}))
    app.dependency_overrides[get_llm_client] = lambda: FakeLLMClient()
    with TestClient(app) as client:
        response = client.get("/secret.txt")
    dispose_databases()

    assert response.status_code == 404


def test_unusable_storage_still_starts_and_reports_db_error(
    settings: Settings, tmp_path: Path
) -> None:
    """A storage failure at startup must not take down /api/health (persistence.md)."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("", encoding="utf-8")
    broken = settings.model_copy(
        update={
            "database_url": f"sqlite:///{blocker / 'budgie.db'}",
            "upload_dir": str(blocker / "uploads"),
        }
    )

    app = create_app(broken)
    app.dependency_overrides[get_llm_client] = lambda: FakeLLMClient()
    with TestClient(app) as client:
        response = client.get("/api/health")
    dispose_databases()

    assert response.status_code == 200
    assert response.json()["db"] == "error"

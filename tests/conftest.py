"""Shared fixtures: an app on a temporary SQLite database with a fake model server."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.dependencies import dispose_databases, get_llm_client


class FakeLLMClient:
    """Stands in for LLMClient; never touches the network."""

    def __init__(self, up: bool = False) -> None:
        self.up = up

    def ping(self) -> bool:
        return self.up


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'budgie.db'}",
        upload_dir=str(tmp_path / "uploads"),
        frontend_dir=str(tmp_path / "no-frontend"),
    )


@pytest.fixture
def fake_llm() -> FakeLLMClient:
    return FakeLLMClient(up=False)


@pytest.fixture
def client(settings: Settings, fake_llm: FakeLLMClient) -> Iterator[TestClient]:
    app = create_app(settings)
    app.dependency_overrides[get_llm_client] = lambda: fake_llm
    with TestClient(app) as test_client:
        yield test_client
    dispose_databases()

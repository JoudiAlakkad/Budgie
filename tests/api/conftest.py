"""Fixtures for API tests that run the extraction pipeline against a replayed model."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.dependencies import dispose_databases, get_llm_client, get_today
from tests.api.helpers import TODAY, ModelServer


@pytest.fixture
def model() -> ModelServer:
    return ModelServer()


@pytest.fixture
def api(settings: Settings, model: ModelServer) -> Iterator[TestClient]:
    """The app with a real `LLMClient` over `model` and today fixed to 2026-10-05.

    Starlette's TestClient runs background tasks before a call returns, so a receipt
    is already `extracted` or `failed` when `upload` returns.
    """
    app = create_app(settings)
    app.dependency_overrides[get_llm_client] = model.client
    app.dependency_overrides[get_today] = lambda: TODAY
    with TestClient(app) as client:
        yield client
    dispose_databases()

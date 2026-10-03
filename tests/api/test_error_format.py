"""Every non-2xx response uses `{error, detail, fields}` (contracts/error-format.md)."""

from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.api.errors import STATUS_BY_CODE
from app.config import Settings
from app.errors import (
    BudgieError,
    FileTooLarge,
    IncompleteExpense,
    InvalidState,
    NotFound,
    NotImplementedYet,
    StorageError,
    UncategorizedItems,
    UnsupportedFile,
)
from app.main import create_app
from app.services.dependencies import dispose_databases, get_llm_client
from tests.conftest import FakeLLMClient

ERRORS: list[BudgieError] = [
    NotFound(),
    InvalidState(),
    FileTooLarge(),
    UnsupportedFile(),
    UncategorizedItems(),
    IncompleteExpense(),
    NotImplementedYet("F05"),
    StorageError(),
]


@pytest.fixture
def error_client(settings: Settings) -> Iterator[TestClient]:
    """The real app plus test-only routes that raise each kind of error."""
    app = create_app(settings)
    app.dependency_overrides[get_llm_client] = lambda: FakeLLMClient()
    router = APIRouter()

    def raiser(error: BudgieError) -> Callable[[], None]:
        def endpoint() -> None:
            raise error

        return endpoint

    for index, error in enumerate(ERRORS):
        router.add_api_route(f"/test-error/{index}", raiser(error), methods=["GET"])

    @router.get("/test-crash")
    def crash() -> None:
        raise RuntimeError("secret /srv/budgie/data/budgie.db")

    @router.get("/test-storage-cause")
    def storage_cause() -> None:
        try:
            raise OSError("secret /srv/budgie/data/uploads")
        except OSError as exc:
            raise StorageError("The upload directory is not usable.") from exc

    app.include_router(router, prefix="/api")
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
    dispose_databases()


def _assert_error(response: httpx.Response, status: int, code: str) -> dict:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error", "detail", "fields"}
    assert body["error"] == code
    assert isinstance(body["detail"], str) and body["detail"]
    return body


@pytest.mark.parametrize("index", range(len(ERRORS)), ids=[type(e).__name__ for e in ERRORS])
def test_budgie_errors_use_their_status_and_the_error_body(
    error_client: TestClient, index: int
) -> None:
    error = ERRORS[index]
    body = _assert_error(
        error_client.get(f"/api/test-error/{index}"), STATUS_BY_CODE[error.code], error.code
    )

    assert body["detail"] == error.detail
    assert body["fields"] is None


def test_not_implemented_names_the_feature(error_client: TestClient) -> None:
    index = next(i for i, e in enumerate(ERRORS) if isinstance(e, NotImplementedYet))
    body = _assert_error(error_client.get(f"/api/test-error/{index}"), 501, "not_implemented")

    assert "F05" in body["detail"]


def test_storage_error_is_500_without_its_cause(error_client: TestClient) -> None:
    response = error_client.get("/api/test-storage-cause")
    body = _assert_error(response, 500, "storage_error")

    assert body["detail"] == "The upload directory is not usable."
    assert "secret" not in response.text
    assert "/srv" not in response.text


def test_unexpected_exception_is_500_without_internals(error_client: TestClient) -> None:
    response = error_client.get("/api/test-crash")
    body = _assert_error(response, 500, "internal_error")

    assert body["fields"] is None
    for leak in ("Traceback", "secret", "/srv", "RuntimeError", "budgie.db"):
        assert leak not in response.text


def test_unknown_path_is_404_not_found(client: TestClient) -> None:
    _assert_error(client.get("/api/nope"), 404, "not_found")


def test_unknown_path_with_frontend_mount_is_404_not_found(
    settings: Settings, tmp_path: Path
) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<h1>Budgie</h1>", encoding="utf-8")
    app = create_app(settings.model_copy(update={"frontend_dir": str(frontend)}))
    with TestClient(app) as client:
        response = client.get("/api/nope")
    dispose_databases()

    _assert_error(response, 404, "not_found")


def test_wrong_method_is_405_method_not_allowed(client: TestClient) -> None:
    response = client.patch("/api/receipts")

    _assert_error(response, 405, "method_not_allowed")
    assert response.headers["allow"]


def test_invalid_body_is_422_with_fields(client: TestClient) -> None:
    payload = {
        "merchant": "REWE",
        "date": "2026-10-03",
        "total": 1.99,
        "line_items": [{"description": "BIO BANANE", "amount": "lots"}],
    }
    body = _assert_error(client.post("/api/expenses", json=payload), 422, "validation_error")

    assert body["fields"][0]["field"] == "line_items.0.amount"
    assert body["fields"][0]["message"]


def test_unknown_field_is_422(client: TestClient) -> None:
    body = _assert_error(
        client.put("/api/goal", json={"target_amount": 1, "target_date": "2027-01-01", "x": 1}),
        422,
        "validation_error",
    )

    assert [f["field"] for f in body["fields"]] == ["x"]


def test_invalid_query_and_path_are_422_with_fields(client: TestClient) -> None:
    month = _assert_error(client.get("/api/insights/summary?month=2026-1"), 422, "validation_error")
    path = _assert_error(client.get("/api/receipts/abc"), 422, "validation_error")

    assert month["fields"][0]["field"] == "query.month"
    assert path["fields"][0]["field"] == "path.id"


def test_malformed_json_is_422_validation_error(client: TestClient) -> None:
    response = client.post(
        "/api/expenses", content=b'{"merchant": ', headers={"content-type": "application/json"}
    )

    body = _assert_error(response, 422, "validation_error")
    assert body["fields"]


def test_broken_multipart_is_400_bad_request(client: TestClient) -> None:
    """Starlette can't parse a multipart body without a boundary; FastAPI answers 400."""
    response = client.post(
        "/api/receipts",
        content=b'--xyz\r\nContent-Disposition: form-data; name="file"\r\n\r\nx\r\n--xyz--\r\n',
        headers={"content-type": "multipart/form-data"},
    )

    body = _assert_error(response, 400, "bad_request")
    assert body["fields"] is None

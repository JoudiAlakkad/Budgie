"""Every non-2xx response uses `{error, detail, fields}` (contracts/error-format.md)."""

from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

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


class _UnmappedError(BudgieError):
    """A subclass someone forgot to add to STATUS_BY_CODE and ErrorCode."""

    code = "made_up_code"


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

    @router.get("/test-teapot")
    def teapot() -> None:
        raise StarletteHTTPException(418, "secret /srv/x")

    @router.get("/test-unmapped-code")
    def unmapped_code() -> None:
        raise _UnmappedError("secret /srv/y")

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
        error_client.get(f"/api/test-error/{index}"),
        STATUS_BY_CODE[error.code],
        error.code,
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


def test_unexpected_exception_is_500_without_internals(
    error_client: TestClient,
) -> None:
    response = error_client.get("/api/test-crash")
    body = _assert_error(response, 500, "internal_error")

    assert body["fields"] is None
    for leak in ("Traceback", "secret", "/srv", "RuntimeError", "budgie.db"):
        assert leak not in response.text


def test_unmapped_http_exception_is_500_without_its_detail(
    error_client: TestClient,
) -> None:
    response = error_client.get("/api/test-teapot")
    body = _assert_error(response, 500, "internal_error")

    assert "secret" not in response.text
    assert "/srv" not in response.text
    assert body["detail"] == "An unexpected error occurred."


def test_unmapped_budgie_error_code_is_500_internal_error(
    error_client: TestClient,
) -> None:
    response = error_client.get("/api/test-unmapped-code")
    body = _assert_error(response, 500, "internal_error")

    assert "secret" not in response.text
    assert body["detail"] == "An unexpected error occurred."


@pytest.fixture(params=["api-only", "with-frontend"])
def routing_client(
    request: pytest.FixtureRequest, settings: Settings, tmp_path: Path
) -> Iterator[TestClient]:
    """The app without and with the static frontend mounted at `/` (as in the Dockerfile)."""
    if request.param == "with-frontend":
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "index.html").write_text("<h1>Budgie</h1>", encoding="utf-8")
        settings = settings.model_copy(update={"frontend_dir": str(frontend)})
    app = create_app(settings)
    app.dependency_overrides[get_llm_client] = lambda: FakeLLMClient()
    with TestClient(app) as client:
        yield client
    dispose_databases()


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
@pytest.mark.parametrize("path", ["/api/nope", "/api", "/api/receipts/1/nope"])
def test_unknown_api_path_is_404_not_found(
    routing_client: TestClient, method: str, path: str
) -> None:
    body = _assert_error(routing_client.request(method, path), 404, "not_found")

    assert body["detail"] == "The requested resource does not exist."


@pytest.mark.parametrize(
    ("method", "path", "allowed"),
    [
        ("PATCH", "/api/receipts", {"GET", "POST"}),
        ("GET", "/api/receipts/1/extract", {"POST"}),
        ("POST", "/api/health", {"GET"}),
        ("DELETE", "/api/budgets", {"GET", "PUT"}),
        # `{id:int}` keeps `export.csv` from matching the id routes.
        ("DELETE", "/api/expenses/export.csv", {"GET"}),
        ("PATCH", "/api/expenses/export.csv", {"GET"}),
    ],
)
def test_wrong_method_is_405_with_allow_header(
    routing_client: TestClient, method: str, path: str, allowed: set[str]
) -> None:
    response = routing_client.request(method, path)
    body = _assert_error(response, 405, "method_not_allowed")

    assert body["detail"] == "This method is not allowed for this path."
    # Starlette lists the methods of the first route whose path matches.
    allow = {m.strip() for m in response.headers["allow"].split(",")}
    assert allow and allow <= allowed


def test_frontend_mount_still_serves_frontend_and_docs(settings: Settings, tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<h1>Budgie</h1>", encoding="utf-8")
    (frontend / "apiary.js").write_text("// not under /api/", encoding="utf-8")
    app = create_app(settings.model_copy(update={"frontend_dir": str(frontend)}))
    app.dependency_overrides[get_llm_client] = lambda: FakeLLMClient()
    with TestClient(app) as client:
        index = client.get("/")
        script = client.get("/apiary.js")
        docs = client.get("/docs")
        spec = client.get("/openapi.json")
        health = client.get("/api/health")
    dispose_databases()

    assert "Budgie" in index.text
    assert script.status_code == 200
    assert docs.status_code == 200
    assert "/api/receipts" in spec.json()["paths"]
    assert health.json()["status"] == "ok"


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


def test_invalid_query_is_422_with_prefixed_field(client: TestClient) -> None:
    month = _assert_error(client.get("/api/insights/summary?month=2026-1"), 422, "validation_error")

    assert month["fields"][0]["field"] == "query.month"


@pytest.mark.parametrize("path", ["/api/receipts/abc", "/api/expenses/abc"])
def test_non_integer_id_is_404_not_found(client: TestClient, path: str) -> None:
    """`{id:int}` doesn't match, so the id is an unknown path, not a validation error."""
    _assert_error(client.get(path), 404, "not_found")


@pytest.mark.parametrize("month", ["2026-13", "2026-00", "2026-1", "26-10", "2026-10-01"])
@pytest.mark.parametrize("route", ["summary", "leaks"])
def test_impossible_month_is_422(client: TestClient, route: str, month: str) -> None:
    body = _assert_error(
        client.get(f"/api/insights/{route}?month={month}"), 422, "validation_error"
    )

    assert body["fields"][0]["field"] == "query.month"


@pytest.mark.parametrize("month", ["2026-01", "2026-12"])
def test_valid_month_reaches_the_route(client: TestClient, month: str) -> None:
    """The summary is built (F08); leaks are still a stub (F09)."""
    assert client.get(f"/api/insights/summary?month={month}").status_code == 200
    assert client.get(f"/api/insights/leaks?month={month}").status_code == 501


def test_malformed_json_is_422_validation_error(client: TestClient) -> None:
    response = client.post(
        "/api/expenses",
        content=b'{"merchant": ',
        headers={"content-type": "application/json"},
    )

    body = _assert_error(response, 422, "validation_error")
    assert [f["field"] for f in body["fields"]] == ["body"]


def test_broken_multipart_is_400_bad_request(client: TestClient) -> None:
    """Starlette can't parse a multipart body without a boundary; FastAPI answers 400."""
    response = client.post(
        "/api/receipts",
        content=b'--xyz\r\nContent-Disposition: form-data; name="file"\r\n\r\nx\r\n--xyz--\r\n',
        headers={"content-type": "multipart/form-data"},
    )

    body = _assert_error(response, 400, "bad_request")
    assert body["fields"] is None


def test_upload_form_rejects_missing_file_and_unknown_fields(
    client: TestClient,
) -> None:
    body = _assert_error(
        client.post("/api/receipts", files={"other": ("r.jpg", b"\xff\xd8\xff", "image/jpeg")}),
        422,
        "validation_error",
    )

    assert {f["field"] for f in body["fields"]} == {"file", "other"}

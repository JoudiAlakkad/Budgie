"""The generated spec matches contracts/api-endpoints.md in both directions (decision 0016)."""

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ENDPOINTS_MD = Path(__file__).resolve().parents[2] / "docs/wiki/contracts/api-endpoints.md"
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
ROW = re.compile(r"^\|\s*([A-Z/]+)\s*\|\s*`([^`]+)`\s*\|")


def documented_operations() -> set[tuple[str, str]]:
    """(METHOD, /api/path) for every row of the endpoint tables."""
    operations = set()
    for line in ENDPOINTS_MD.read_text(encoding="utf-8").splitlines():
        match = ROW.match(line)
        if not match:
            continue
        methods, path = match.groups()
        for method in methods.split("/"):
            assert method in HTTP_METHODS, line
            operations.add((method, "/api" + path))
    return operations


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return create_app(Settings(_env_file=None, frontend_dir="")).openapi()


def spec_operations(spec: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, operations in spec["paths"].items()
        for method in operations
    }


def test_endpoint_tables_are_parsed() -> None:
    operations = documented_operations()

    assert len(operations) == 23
    assert ("GET", "/api/expenses/export.csv") in operations
    assert ("GET", "/api/health") in operations


def test_every_documented_endpoint_is_in_the_spec(spec: dict[str, Any]) -> None:
    assert documented_operations() - spec_operations(spec) == set()


def test_nothing_undocumented_is_in_the_spec(spec: dict[str, Any]) -> None:
    assert spec_operations(spec) - documented_operations() == set()


def test_every_api_operation_documents_the_error_body(spec: dict[str, Any]) -> None:
    ref = {"$ref": "#/components/schemas/ErrorBody"}
    for path, operations in spec["paths"].items():
        if path == "/api/health":
            continue
        for method, operation in operations.items():
            for status in ("422", "500"):
                response = operation["responses"].get(status)
                assert response, f"{method.upper()} {path} has no {status}"
                assert response["content"]["application/json"]["schema"] == ref
            assert "501" not in operation["responses"]


def test_default_validation_error_schema_is_gone(spec: dict[str, Any]) -> None:
    schemas = spec["components"]["schemas"]

    assert "HTTPValidationError" not in schemas
    assert "ValidationError" not in schemas
    assert "ErrorBody" in schemas


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("post", "/api/receipts", "202"),
        ("post", "/api/receipts/{id}/extract", "202"),
        ("post", "/api/expenses", "201"),
        ("delete", "/api/receipts/{id}", "204"),
        ("delete", "/api/expenses/{id}", "204"),
        ("delete", "/api/item-categories/{normalized_name}", "204"),
        ("get", "/api/receipts/{id}", "404"),
        ("post", "/api/receipts", "413"),
        ("post", "/api/receipts/{id}/extract", "409"),
        ("delete", "/api/item-categories/{normalized_name}", "409"),
        ("get", "/api/goal", "404"),
    ],
)
def test_status_codes(spec: dict[str, Any], method: str, path: str, status: str) -> None:
    assert status in spec["paths"][path][method]["responses"]


def test_no_content_routes_have_no_body(spec: dict[str, Any]) -> None:
    for path, method in [
        ("/api/receipts/{id}", "delete"),
        ("/api/expenses/{id}", "delete"),
        ("/api/item-categories/{normalized_name}", "delete"),
    ]:
        assert "content" not in spec["paths"][path][method]["responses"]["204"]


def test_media_types(spec: dict[str, Any]) -> None:
    csv = spec["paths"]["/api/expenses/export.csv"]["get"]["responses"]["200"]["content"]
    image = spec["paths"]["/api/receipts/{id}/image"]["get"]["responses"]["200"]["content"]

    assert list(csv) == ["text/csv"]
    assert set(image) == {"image/jpeg", "image/png", "image/webp"}


def test_from_query_parameter_is_named_from(spec: dict[str, Any]) -> None:
    for path in ("/api/expenses", "/api/expenses/export.csv"):
        names = {p["name"] for p in spec["paths"][path]["get"]["parameters"]}
        assert {"from", "to"} <= names


EXPENSE = {
    "merchant": "REWE",
    "date": "2026-10-03",
    "total": 1.99,
    "line_items": [{"description": "BIO BANANE 1 KG", "amount": 1.99}],
}

STUBS: list[tuple[str, str, dict[str, Any], str]] = [
    ("POST", "/api/receipts", {"files": {"file": ("r.jpg", b"\xff\xd8\xff", "image/jpeg")}}, "F05"),
    ("GET", "/api/receipts?status=failed", {}, "F05"),
    ("GET", "/api/receipts/1", {}, "F05"),
    ("GET", "/api/receipts/1/image", {}, "F05"),
    ("POST", "/api/receipts/1/extract", {}, "F05"),
    ("DELETE", "/api/receipts/1", {}, "F05"),
    ("GET", "/api/expenses?from=2026-10-01&to=2026-10-31&category=drinks", {}, "F05"),
    ("POST", "/api/expenses", {"json": EXPENSE}, "F06"),
    ("GET", "/api/expenses/export.csv?from=2026-10-01", {}, "F10"),
    ("GET", "/api/expenses/1", {}, "F05"),
    ("PATCH", "/api/expenses/1", {"json": {"merchant": "Aldi"}}, "F06"),
    ("POST", "/api/expenses/1/confirm", {}, "F06"),
    ("DELETE", "/api/expenses/1", {}, "F06"),
    ("GET", "/api/item-categories?q=ban", {}, "F07"),
    ("PUT", "/api/item-categories/banane", {"json": {"category": "groceries.fresh"}}, "F07"),
    ("DELETE", "/api/item-categories/banane", {}, "F07"),
    ("GET", "/api/budgets", {}, "F08"),
    (
        "PUT",
        "/api/budgets",
        {"json": [{"category": "snacks_sweets", "monthly_limit": 40}]},
        "F08",
    ),
    ("GET", "/api/goal", {}, "F08"),
    ("PUT", "/api/goal", {"json": {"target_amount": 500, "target_date": "2027-06-30"}}, "F08"),
    ("GET", "/api/insights/summary?month=2026-10", {}, "F08"),
    ("GET", "/api/insights/leaks", {}, "F09"),
]


def test_stub_list_covers_every_documented_endpoint() -> None:
    covered = {(method, path.split("?")[0]) for method, path, _, _ in STUBS}
    concrete = {
        (method, re.sub(r"\{[^}]+\}", lambda m: "banane" if "name" in m[0] else "1", path))
        for method, path in documented_operations()
        if path != "/api/health"
    }

    assert covered == concrete


@pytest.mark.parametrize(
    ("method", "url", "kwargs", "feature"), STUBS, ids=[f"{m} {u}" for m, u, _, _ in STUBS]
)
def test_stub_answers_501_naming_its_feature(
    client: TestClient, method: str, url: str, kwargs: dict[str, Any], feature: str
) -> None:
    response = client.request(method, url, **kwargs)

    assert response.status_code == 501
    body = response.json()
    assert body == {"error": "not_implemented", "detail": body["detail"], "fields": None}
    assert feature in body["detail"]

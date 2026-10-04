"""The generated spec matches contracts/api-endpoints.md in both directions (decision 0016)."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ENDPOINTS_MD = Path(__file__).resolve().parents[2] / "docs/wiki/contracts/api-endpoints.md"
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
# | METHOD | `path` | request | response |; `\|` inside a cell is an escaped pipe.
CELL_SEPARATOR = re.compile(r"(?<!\\)\|")
METHOD_CELL = re.compile(r"^[A-Z]+(/[A-Z]+)*$")
PATH_CELL = re.compile(r"^`(/[^`]*)`$")
STATUS = re.compile(r"`(\d{3})`")
# A DTO is a backticked CapitalisedName, optionally a list: `Receipt`, `Budget[]`.
# `MAX_UPLOAD_MB` (underscores) and `{category: Category}` (braces) don't match.
DTO = re.compile(r"`([A-Z][A-Za-z]*)(\[\])?`")

# Cells written as prose instead of a DTO name, mapped to what the spec must say:
# (media type, component name or None for "no schema check"). Keep this list minimal.
PROSE_REQUESTS: dict[tuple[str, str], tuple[str, str]] = {
    # "multipart `file` (jpeg/png/webp, …)": the form model
    ("POST", "/api/receipts"): ("multipart/form-data", "ReceiptUpload"),
    # "`{category: Category}`": an inline object in the table
    ("PUT", "/api/item-categories/{normalized_name}"): ("application/json", "ItemCategoryUpdate"),
}
PROSE_RESPONSES: dict[tuple[str, str], dict[str, str | None]] = {
    # "`{status, db: ok|error, llm: ok|down, model}`"
    ("GET", "/api/health"): {"application/json": "Health"},
    # "`text/csv` ([csv-export](csv-export.md))"
    ("GET", "/api/expenses/export.csv"): {"text/csv": None},
    # "image bytes (`image/jpeg|png|webp`)"
    ("GET", "/api/receipts/{id}/image"): dict.fromkeys(["image/jpeg", "image/png", "image/webp"]),
}


@dataclass(frozen=True)
class Row:
    method: str
    path: str
    request: str
    response: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)

    @property
    def statuses(self) -> list[str]:
        return STATUS.findall(self.response)

    @property
    def request_dtos(self) -> list[tuple[str, bool]]:
        return [(name, bool(is_list)) for name, is_list in DTO.findall(self.request)]

    @property
    def response_dtos(self) -> list[tuple[str, bool]]:
        return [(name, bool(is_list)) for name, is_list in DTO.findall(self.response)]


def documented_rows() -> list[Row]:
    """One Row per method of every row of the endpoint tables."""
    rows = []
    for line in ENDPOINTS_MD.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip().replace("\\|", "|") for cell in CELL_SEPARATOR.split(line)[1:-1]]
        if len(cells) != 4 or not METHOD_CELL.match(cells[0]):
            continue  # header, separator or another table
        path_match = PATH_CELL.match(cells[1])
        assert path_match, line
        methods, _, request, response = cells
        for method in methods.split("/"):
            assert method in HTTP_METHODS, line
            rows.append(Row(method, "/api" + path_match.group(1), request, response))
    return rows


def documented_operations() -> set[tuple[str, str]]:
    """(METHOD, /api/path) for every row of the endpoint tables."""
    return {row.key for row in documented_rows()}


ROWS = documented_rows()


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return create_app(Settings(_env_file=None, frontend_dir="")).openapi()


def spec_operations(spec: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, operations in spec["paths"].items()
        for method in operations
    }


def _schema(name: str, is_list: bool) -> dict[str, Any]:
    ref = {"$ref": f"#/components/schemas/{name}"}
    return {"type": "array", "items": ref} if is_list else ref


def _without_titles(content: dict[str, Any]) -> dict[str, Any]:
    """FastAPI adds a generated `title` to inline (list) schemas; it isn't part of the shape."""
    return {
        media_type: {
            **entry,
            "schema": {k: v for k, v in entry["schema"].items() if k != "title"},
        }
        for media_type, entry in content.items()
    }


def _operation(spec: dict[str, Any], row: Row) -> dict[str, Any]:
    return spec["paths"][row.path][row.method.lower()]


def test_endpoint_tables_are_parsed() -> None:
    operations = documented_operations()

    assert len(operations) == len(ROWS)
    assert ("GET", "/api/expenses/export.csv") in operations
    assert ("GET", "/api/health") in operations
    assert all(row.statuses for row in ROWS), "every row documents its success status"
    assert set(PROSE_REQUESTS) | set(PROSE_RESPONSES) <= operations


@pytest.mark.parametrize("row", ROWS, ids=lambda row: f"{row.method} {row.path}")
def test_documented_statuses_match_the_spec(spec: dict[str, Any], row: Row) -> None:
    """Success status plus documented errors (plus the shared 422/500) are exactly the spec's."""
    success, *errors = row.statuses
    responses = set(_operation(spec, row)["responses"])
    expected = {success, *errors}
    if row.path != "/api/health":
        expected |= {"422", "500"}

    assert responses == expected
    assert min(responses) == success


@pytest.mark.parametrize("row", ROWS, ids=lambda row: f"{row.method} {row.path}")
def test_documented_request_body_matches_the_spec(spec: dict[str, Any], row: Row) -> None:
    body = _operation(spec, row).get("requestBody")

    if row.key in PROSE_REQUESTS:
        media_type, name = PROSE_REQUESTS[row.key]
        assert row.request_dtos == []
        assert _without_titles(body["content"]) == {media_type: {"schema": _schema(name, False)}}
    elif row.request_dtos:
        [(name, is_list)] = row.request_dtos
        expected = {"application/json": {"schema": _schema(name, is_list)}}
        assert _without_titles(body["content"]) == expected
    else:
        assert body is None, f"{row.request!r} documents no body"


@pytest.mark.parametrize("row", ROWS, ids=lambda row: f"{row.method} {row.path}")
def test_documented_response_body_matches_the_spec(spec: dict[str, Any], row: Row) -> None:
    success = row.statuses[0]
    content = _operation(spec, row)["responses"][success].get("content")

    if row.key in PROSE_RESPONSES:
        expected = PROSE_RESPONSES[row.key]
        assert row.response_dtos == []
        assert set(content) == set(expected)
        for media_type, name in expected.items():
            if name:
                assert _without_titles(content)[media_type]["schema"] == _schema(name, False)
    elif row.response_dtos:
        [(name, is_list)] = row.response_dtos
        assert _without_titles(content) == {"application/json": {"schema": _schema(name, is_list)}}
    else:
        assert success == "204", f"{row.response!r} documents no body"
        assert content is None


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


def test_parser_reads_the_cells_it_should() -> None:
    """Spot checks, so a parser bug can't make the table-driven tests vacuous."""
    rows = {row.key: row for row in ROWS}

    assert rows[("POST", "/api/receipts")].statuses == ["202", "413", "422"]
    assert rows[("POST", "/api/expenses")].request_dtos == [("ExpenseCreate", False)]
    assert rows[("PUT", "/api/budgets")].request_dtos == [("Budget", True)]
    assert rows[("GET", "/api/expenses")].response_dtos == [("Expense", True)]
    assert rows[("DELETE", "/api/receipts/{id}")].statuses == ["204", "404"]


def test_upload_form_has_a_stable_name(spec: dict[str, Any]) -> None:
    schemas = spec["components"]["schemas"]

    assert "ReceiptUpload" in schemas
    assert not [name for name in schemas if name.startswith("Body_")]


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

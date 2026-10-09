"""`GET /expenses/export.csv` (contracts/csv-export.md; decision 0024)."""

import csv
import io
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import Settings
from app.domain.csv_export import COLUMNS
from app.services.dependencies import database_for

URL = "/api/expenses/export.csv"
HEADER = ",".join(COLUMNS) + "\r\n"


def add(
    client: TestClient,
    day: str,
    items: list[dict[str, Any]],
    *,
    merchant: str = "REWE",
    confirm: bool = True,
) -> int:
    """A manual expense with categorised items; confirmed unless `confirm` is False."""
    body = {
        "merchant": merchant,
        "date": day,
        "total": round(sum(item["amount"] for item in items), 2),
        "line_items": [{"category": "groceries.fresh"} | item for item in items],
    }
    created = client.post("/api/expenses", json=body)
    assert created.status_code == 201, created.text
    expense_id = int(created.json()["id"])
    if confirm:
        confirmed = client.post(f"/api/expenses/{expense_id}/confirm")
        assert confirmed.status_code == 200, confirmed.text
    return expense_id


def export(client: TestClient, **params: str) -> httpx.Response:
    renamed = {("from" if key == "from_" else key): value for key, value in params.items()}
    response = client.get(URL, params=renamed)
    assert response.status_code == 200, response.text
    return response


def rows(response: httpx.Response) -> list[dict[str, str]]:
    parsed = list(csv.reader(io.StringIO(response.content.decode("utf-8"), newline="")))
    assert parsed[0] == list(COLUMNS)
    return [dict(zip(COLUMNS, line, strict=True)) for line in parsed[1:]]


def test_response_headers(client: TestClient) -> None:
    response = export(client, from_="2026-10-01", to="2026-10-31")

    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == (
        'attachment; filename="budgie-expenses_2026-10-01_2026-10-31.csv"'
    )
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("params", "filename"),
    [
        ({}, "budgie-expenses_all_all.csv"),
        ({"from_": "2026-10-01"}, "budgie-expenses_2026-10-01_all.csv"),
        ({"to": "2026-10-31"}, "budgie-expenses_all_2026-10-31.csv"),
    ],
)
def test_filename_says_all_for_a_missing_bound(
    client: TestClient, params: dict[str, str], filename: str
) -> None:
    response = export(client, **params)

    assert response.headers["content-disposition"] == f'attachment; filename="{filename}"'


def test_empty_database_gives_the_header_only(client: TestClient) -> None:
    assert export(client).content == HEADER.encode("utf-8")


def test_from_after_to_gives_the_header_only(client: TestClient) -> None:
    add(client, "2026-10-03", [{"description": "BANANE", "amount": 1.99}])

    response = export(client, from_="2026-10-05", to="2026-10-01")

    assert response.content == HEADER.encode("utf-8")


def test_one_row_per_item_in_position_order(client: TestClient) -> None:
    expense_id = add(
        client,
        "2026-10-03",
        [
            {"description": "BIO BANANE", "amount": 1.99, "qty": 1.234, "unit": "kg"},
            {"description": "MILCH", "amount": 1.09, "qty": 2},
            {"description": "BROT", "amount": 2.49},
        ],
        merchant="Bäckerei Müller",
    )

    exported = rows(export(client))

    assert exported == [
        {
            "expense_id": str(expense_id),
            "date": "2026-10-03",
            "merchant": "Bäckerei Müller",
            "currency": "EUR",
            "expense_total": "5.57",
            "item_description": "BIO BANANE",
            "item_normalized_name": exported[0]["item_normalized_name"],
            "item_qty": "1.234",
            "item_unit": "kg",
            "item_amount": "1.99",
            "category": "groceries.fresh",
            "source": "manual",
        },
        exported[1] | {"item_description": "MILCH", "item_qty": "2", "item_unit": ""},
        exported[2] | {"item_description": "BROT", "item_qty": "", "item_amount": "2.49"},
    ]
    assert [row["item_amount"] for row in exported] == ["1.99", "1.09", "2.49"]
    assert all(row["expense_id"] == str(expense_id) for row in exported)


def test_only_confirmed_expenses_sorted_by_date_then_id(client: TestClient) -> None:
    later = add(client, "2026-10-05", [{"description": "A", "amount": 1.00}])
    first = add(client, "2026-10-01", [{"description": "B", "amount": 2.00}])
    same_day = add(client, "2026-10-01", [{"description": "C", "amount": 3.00}])
    add(client, "2026-10-02", [{"description": "DRAFT", "amount": 4.00}], confirm=False)

    exported = rows(export(client))

    assert [row["expense_id"] for row in exported] == [str(first), str(same_day), str(later)]
    assert "DRAFT" not in {row["item_description"] for row in exported}


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"from_": "2026-10-02", "to": "2026-10-04"}, ["2026-10-02", "2026-10-04"]),
        ({"from_": "2026-10-04"}, ["2026-10-04", "2026-10-05"]),
        ({"to": "2026-10-02"}, ["2026-10-01", "2026-10-02"]),
        ({"from_": "2026-10-03", "to": "2026-10-03"}, []),
        ({}, ["2026-10-01", "2026-10-02", "2026-10-04", "2026-10-05"]),
    ],
)
def test_from_and_to_are_inclusive(
    client: TestClient, params: dict[str, str], expected: list[str]
) -> None:
    for day in ("2026-10-01", "2026-10-02", "2026-10-04", "2026-10-05"):
        add(client, day, [{"description": "X", "amount": 1.00}])

    assert [row["date"] for row in rows(export(client, **params))] == expected


@pytest.mark.parametrize("name", ["from", "to"])
@pytest.mark.parametrize("value", ["2026-13-01", "03.10.2026", "yesterday"])
def test_a_bad_date_is_a_validation_error(client: TestClient, name: str, value: str) -> None:
    response = client.get(URL, params={name: value})

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "validation_error"
    assert [field["field"] for field in body["fields"]] == [f"query.{name}"]


def test_deposit_and_discount_are_included_and_negatives_stay_bare(client: TestClient) -> None:
    add(
        client,
        "2026-10-03",
        [
            {"description": "COLA", "amount": 1.29},
            {"description": "PFAND", "amount": 0.25, "category": "deposit"},
            {"description": "-20% Rabatt", "amount": -0.25, "category": "discount"},
        ],
    )

    exported = rows(export(client))

    assert [(r["category"], r["item_amount"]) for r in exported] == [
        ("groceries.fresh", "1.29"),
        ("deposit", "0.25"),
        ("discount", "-0.25"),
    ]
    assert exported[2]["item_description"] == "'-20% Rabatt", "formula guard on text"
    assert {row["expense_total"] for row in exported} == {"1.29"}


def test_formula_guard_on_merchant(client: TestClient) -> None:
    add(client, "2026-10-03", [{"description": "=HYPERLINK(1)", "amount": 1.00}], merchant="@evil")

    [row] = rows(export(client))

    assert row["merchant"] == "'@evil"
    assert row["item_description"] == "'=HYPERLINK(1)"


def test_an_unconfirmed_expense_disappears(client: TestClient) -> None:
    kept = add(client, "2026-10-01", [{"description": "KEPT", "amount": 1.00}])
    edited = add(client, "2026-10-02", [{"description": "EDITED", "amount": 2.00}])
    assert {row["expense_id"] for row in rows(export(client))} == {str(kept), str(edited)}

    patched = client.patch(f"/api/expenses/{edited}", json={"merchant": "Aldi"})
    assert patched.status_code == 200
    assert patched.json()["confirmed"] is False

    assert [row["expense_id"] for row in rows(export(client))] == [str(kept)]


def test_quoting_and_crlf_on_the_wire(client: TestClient) -> None:
    add(client, "2026-10-03", [{"description": 'Pizza 12", Salami', "amount": 7.50}])

    body = export(client).content.decode("utf-8")

    assert body.startswith(HEADER)
    assert ',"Pizza 12"", Salami",' in body
    assert body.endswith("\r\n")
    assert "\n" not in body.replace("\r\n", "")


@pytest.mark.parametrize(
    ("merchant", "description", "wire"),
    [
        ('REWE;=HYPERLINK("x")', "BROT", ',"REWE;=HYPERLINK(""x"")",EUR,2.49,BROT,'),
        ("Aldi; Süd", "A;B", ',"Aldi; Süd",EUR,2.49,"A;B",'),
        ("REWE", " =1+1", ",REWE,EUR,2.49,' =1+1,"),
        ("＠evil", "＝SUM(A1)", ",'＠evil,EUR,2.49,'＝SUM(A1),"),
        ("REWE", " -1;2", ',REWE,EUR,2.49,"\' -1;2",'),
        (" REWE", " BROT", ", REWE,EUR,2.49, BROT,"),
    ],
)
def test_semicolon_quoting_and_formula_guard_on_the_wire(
    client: TestClient, merchant: str, description: str, wire: str
) -> None:
    add(client, "2026-10-03", [{"description": description, "amount": 2.49}], merchant=merchant)

    body = export(client).content.decode("utf-8")

    assert wire in body


def test_built_in_memory_and_no_row_logged(
    client: TestClient, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    add(client, "2026-10-03", [{"description": "Zebrafink Kuchen", "amount": 3.20}])
    before = {path for path in tmp_path.rglob("*") if not path.name.endswith("-journal")}
    caplog.set_level(logging.DEBUG)

    response = export(client)

    assert "Zebrafink Kuchen" in response.text
    after = {path for path in tmp_path.rglob("*") if not path.name.endswith("-journal")}
    assert after == before, "no file written"
    assert all("Zebrafink" not in record.getMessage() for record in caplog.records)


def test_a_storage_error_is_a_json_500_not_a_truncated_file(
    client: TestClient, settings: Settings
) -> None:
    add(client, "2026-10-03", [{"description": "BROT", "amount": 2.49}])
    with database_for(settings.database_url).engine.begin() as conn:
        conn.execute(text("DROP TABLE line_items"))

    response = client.get(URL)

    assert response.status_code == 500
    assert response.headers["content-type"] == "application/json"
    assert response.json()["error"] == "storage_error"
    assert "content-disposition" not in response.headers

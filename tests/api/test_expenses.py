"""`GET /expenses`, `POST /expenses` (manual entry) and `GET /expenses/{id}` (F05)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import Settings
from app.db.repositories.receipts import ReceiptRepository
from app.services.dependencies import database_for, get_receipt_pipeline
from tests.api.helpers import ModelServer, NoopPipeline, answer, inline_case, stored, upload
from tests.recorded import load_case

MANUAL: dict[str, Any] = {
    "merchant": "REWE",
    "date": "2026-10-03",
    "total": 3.68,
    "line_items": [
        {"description": "BIO  BANANE 1 KG", "amount": 1.99, "qty": 1.234, "unit": "kg"},
        {"description": "Cola", "amount": 1.69, "category": "drinks"},
    ],
}


def create(api: TestClient, **changes: Any) -> dict:
    response = api.post("/api/expenses", json=MANUAL | changes)
    assert response.status_code == 201, response.text
    return response.json()


def count(settings: Settings, table: str) -> int:
    with database_for(settings.database_url).engine.connect() as conn:
        return int(conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())


def failed_receipt(api: TestClient, model: ModelServer) -> int:
    model.serve(load_case("not_a_receipt"))
    receipt_id = upload(api).json()["id"]
    assert api.get(f"/api/receipts/{receipt_id}").json()["status"] == "failed"
    return int(receipt_id)


# ---------------------------------------------------------------- create


def test_manual_expense_is_created_assessed_and_returned(api: TestClient) -> None:
    expense = create(api)

    assert expense == {
        "id": expense["id"],
        "receipt_id": None,
        "merchant": "REWE",
        "date": "2026-10-03",
        "currency": "EUR",
        "total": 3.68,
        "subtotal": None,
        "tax": None,
        "source": "manual",
        "review_status": "needs_review",
        "confirmed": False,
        "flags": [
            {
                "field": "line_items[0]",
                "code": "uncategorized_item",
                "message": 'The item "BIO  BANANE 1 KG" has no category yet.',
            }
        ],
        "line_items": [
            {
                "id": expense["line_items"][0]["id"],
                "description": "BIO  BANANE 1 KG",
                "normalized_name": "bio banane 1 kg",
                "qty": 1.234,  # unrounded
                "unit": "kg",
                "unit_price": None,
                "amount": 1.99,
                "category": "uncategorized",
                "category_source": "none",
            },
            {
                "id": expense["line_items"][1]["id"],
                "description": "Cola",
                "normalized_name": "cola",
                "qty": None,
                "unit": None,
                "unit_price": None,
                "amount": 1.69,
                "category": "drinks",
                "category_source": "user",
            },
        ],
    }
    assert api.get(f"/api/expenses/{expense['id']}").json() == expense


def test_fully_categorised_consistent_expense_is_accepted(api: TestClient) -> None:
    items = [{**item, "category": "groceries.fresh"} for item in MANUAL["line_items"]]

    expense = create(api, line_items=items, currency="CHF", subtotal=3.68, tax=0.24)

    assert (expense["review_status"], expense["flags"]) == ("accepted", [])
    assert all(item["category_source"] == "user" for item in expense["line_items"])
    assert (expense["currency"], expense["subtotal"], expense["tax"]) == ("CHF", 3.68, 0.24)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"total": 9.99}, "sum_mismatch"),
        ({"date": "2026-10-06"}, "date_in_future"),
        ({"date": "2024-10-04"}, "date_too_old"),
        ({"merchant": "  "}, "missing_merchant"),
    ],
)
def test_the_rules_run_on_create(api: TestClient, changes: dict, code: str) -> None:
    expense = create(api, **changes)

    assert code in [flag["code"] for flag in expense["flags"]]
    assert expense["review_status"] == "needs_review"


def test_a_typed_merchant_is_stored_as_typed(api: TestClient) -> None:
    merchant = "Kiosk Tel. 0231 123456\nwww.kiosk.example"

    assert create(api, merchant=merchant)["merchant"] == merchant


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"line_items": []}, "line_items"),
        ({"total": 1.999}, "total"),
        ({"surprise": 1}, "surprise"),
        ({"currency": "eur"}, "currency"),
        (
            {"line_items": [{"description": "X", "amount": 1, "category": "food"}]},
            "line_items.0.category",
        ),
    ],
)
def test_invalid_body_is_422(
    api: TestClient, settings: Settings, changes: dict, field: str
) -> None:
    response = api.post("/api/expenses", json=MANUAL | changes)

    assert response.status_code == 422
    assert field in [f["field"] for f in response.json()["fields"]]
    assert count(settings, "expenses") == 0


# ---------------------------------------------------------------- enter manually (0015)


def test_entering_a_failed_receipt_by_hand_makes_it_extracted(
    api: TestClient, settings: Settings, model: ModelServer
) -> None:
    receipt_id = failed_receipt(api, model)
    before = stored(settings, receipt_id)
    assert before.raw_model_output is not None and before.model_name is not None

    expense = create(api, receipt_id=receipt_id)

    assert (expense["receipt_id"], expense["source"]) == (receipt_id, "manual")
    after = stored(settings, receipt_id)
    assert (after.error, after.model_name, after.prompt_version) == (None, None, None)
    assert (after.latency_ms, after.raw_model_output) == (None, None)
    receipt = api.get(f"/api/receipts/{receipt_id}").json()
    assert (receipt["status"], receipt["error"], receipt["error_detail"]) == (
        "extracted",
        None,
        None,
    )
    assert receipt["expense"] == expense
    # The photo stays linked.
    assert api.get(f"/api/receipts/{receipt_id}/image").status_code == 200


def test_manual_entry_for_an_unknown_receipt_is_404(api: TestClient, settings: Settings) -> None:
    response = api.post("/api/expenses", json=MANUAL | {"receipt_id": 999})

    assert response.status_code == 404
    assert response.json()["detail"] == "Receipt 999 does not exist."
    assert count(settings, "expenses") == 0


@pytest.mark.parametrize("status", ["uploaded", "extracting", "extracted", "confirmed"])
def test_manual_entry_for_a_receipt_that_is_not_failed_is_409(
    api: TestClient, settings: Settings, status: str
) -> None:
    api.app.dependency_overrides[get_receipt_pipeline] = NoopPipeline  # type: ignore[attr-defined]
    receipt_id = upload(api).json()["id"]
    path = {"extracting": ["extracting"], "extracted": ["extracted"]}
    path["confirmed"] = ["extracted", "confirmed"]
    previous = "uploaded"
    for step in path.get(status, []):
        with database_for(settings.database_url).transaction() as session:
            assert ReceiptRepository(session).transition(receipt_id, previous, step)
        previous = step

    response = api.post("/api/expenses", json=MANUAL | {"receipt_id": receipt_id})

    assert response.status_code == 409
    assert response.json() == {
        "error": "invalid_state",
        "detail": (
            f"Receipt {receipt_id} is {status}; an expense can be entered by hand only for "
            "a failed receipt."
        ),
        "fields": None,
    }
    assert count(settings, "expenses") == 0
    assert stored(settings, receipt_id).status == status


def test_a_receipt_gets_only_one_manual_expense(
    api: TestClient, settings: Settings, model: ModelServer
) -> None:
    receipt_id = failed_receipt(api, model)
    create(api, receipt_id=receipt_id)

    again = api.post("/api/expenses", json=MANUAL | {"receipt_id": receipt_id})

    assert again.status_code == 409
    assert count(settings, "expenses") == 1


# ---------------------------------------------------------------- get and list


def test_get_unknown_expense_is_404(api: TestClient) -> None:
    response = api.get("/api/expenses/999")

    assert response.status_code == 404
    assert response.json() == {
        "error": "not_found",
        "detail": "Expense 999 does not exist.",
        "fields": None,
    }


def test_get_returns_an_extracted_expense(
    api: TestClient, settings: Settings, model: ModelServer
) -> None:
    model.serve(inline_case(answer()))
    receipt = api.get(f"/api/receipts/{upload(api).json()['id']}").json()

    expense = api.get(f"/api/expenses/{receipt['expense']['id']}")

    assert expense.status_code == 200
    assert expense.json() == receipt["expense"]
    assert expense.json()["source"] == "ai"


def test_list_is_newest_date_first_undated_last_ties_by_id(
    api: TestClient, settings: Settings, model: ModelServer
) -> None:
    old = create(api, date="2026-09-01")["id"]
    model.serve(inline_case(answer(date=None)))
    undated = api.get(f"/api/receipts/{upload(api).json()['id']}").json()["expense"]["id"]
    new_a = create(api, date="2026-10-03")["id"]
    new_b = create(api, date="2026-10-03")["id"]

    listed = api.get("/api/expenses")

    assert listed.status_code == 200
    assert [e["id"] for e in listed.json()] == [new_b, new_a, old, undated]


def test_list_filters(api: TestClient) -> None:
    drinks = create(api, date="2026-10-01")["id"]
    plain_items = [{"description": "BROT", "amount": 3.68, "category": "groceries.staples"}]
    accepted = create(api, date="2026-09-30", line_items=plain_items)["id"]
    assert api.get(f"/api/expenses/{accepted}").json()["review_status"] == "accepted"

    def ids(**params: str) -> list[int]:
        response = api.get("/api/expenses", params=params)
        assert response.status_code == 200, response.text
        return [e["id"] for e in response.json()]

    assert ids() == [drinks, accepted]
    assert ids(review_status="accepted") == [accepted]
    assert ids(review_status="needs_review") == [drinks]
    assert ids(confirmed="false") == [drinks, accepted]
    assert ids(confirmed="true") == []
    assert ids(**{"from": "2026-10-01"}) == [drinks]
    assert ids(to="2026-09-30") == [accepted]
    assert ids(**{"from": "2026-09-30", "to": "2026-10-01"}) == [drinks, accepted]
    assert ids(category="drinks") == [drinks]
    assert ids(category="uncategorized") == [drinks]
    assert ids(category="groceries.staples") == [accepted]
    assert ids(category="alcohol") == []


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"review_status": "maybe"}, "query.review_status"),
        ({"category": "food"}, "query.category"),
        ({"from": "03.10.2026"}, "query.from"),
    ],
)
def test_list_rejects_invalid_filters(api: TestClient, params: dict, field: str) -> None:
    response = api.get("/api/expenses", params=params)

    assert response.status_code == 422
    assert response.json()["fields"][0]["field"] == field

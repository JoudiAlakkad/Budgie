"""`GET /expenses`, `POST /expenses` (manual entry) and `GET /expenses/{id}` (F05);
`PATCH /expenses/{id}`, `POST /expenses/{id}/confirm` and `DELETE /expenses/{id}` (F06,
decision 0018)."""

import os
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import Settings
from app.db.images import ImageStore
from app.db.repositories.expenses import ExpenseRepository
from app.db.repositories.receipts import ReceiptRepository
from app.errors import StorageError
from app.services.categorization import CategorizedItem
from app.services.dependencies import database_for, get_item_categorizer, get_receipt_pipeline
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


# ---------------------------------------------------------------- F06 helpers


class LookupCategorizer:
    """Knows `cola` (drinks) and `brot` (groceries.staples) as seed entries."""

    TABLE = {"cola": "drinks", "brot": "groceries.staples"}

    def categorize(self, description: str) -> CategorizedItem:
        name = " ".join(description.lower().split())
        category = self.TABLE.get(name)
        return CategorizedItem(
            normalized_name=name,
            qty=None,
            unit=None,
            category=category or "uncategorized",
            category_source="seed" if category else "none",
        )


def extracted(api: TestClient, model: ModelServer, **fields: Any) -> tuple[int, dict]:
    """A receipt extracted from `answer(**fields)`: its id and its expense."""
    model.serve(inline_case(answer(**fields)))
    receipt = api.get(f"/api/receipts/{upload(api).json()['id']}").json()
    assert receipt["status"] == "extracted", receipt
    return receipt["id"], receipt["expense"]


def patch(api: TestClient, expense_id: int, body: dict) -> dict:
    response = api.patch(f"/api/expenses/{expense_id}", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def confirm(api: TestClient, expense_id: int) -> dict:
    response = api.post(f"/api/expenses/{expense_id}/confirm")
    assert response.status_code == 200, response.text
    return response.json()


def resent(expense: dict, **changes: Any) -> list[dict]:
    """The expense's items as `LineItemInput`s with their ids, plus `changes`."""
    return [
        {"id": item["id"], "description": item["description"], "amount": item["amount"]} | changes
        for item in expense["line_items"]
    ]


def categorised(expense: dict) -> list[dict]:
    return resent(expense, category="groceries.staples")


def codes(expense: dict) -> list[tuple[str | None, str]]:
    return [(flag["field"], flag["code"]) for flag in expense["flags"]]


def unreadable(expense: dict) -> list[str | None]:
    return [field for field, code in codes(expense) if code == "unreadable"]


def receipt_status(api: TestClient, receipt_id: int) -> str:
    return str(api.get(f"/api/receipts/{receipt_id}").json()["status"])


TWO_BREADS = [{"description": "BROT", "qty": 1, "unit_price": 2.49, "amount": 2.49}] * 2


# ---------------------------------------------------------------- PATCH (F06)


def test_patch_changes_only_the_fields_sent(api: TestClient) -> None:
    expense = create(api, subtotal=3.68, tax=0.24)

    edited = patch(api, expense["id"], {"merchant": "Edeka"})

    assert edited == expense | {"merchant": "Edeka"}
    assert api.get(f"/api/expenses/{expense['id']}").json() == edited


def test_patch_can_clear_subtotal_and_tax(api: TestClient) -> None:
    expense = create(api, subtotal=3.68, tax=0.24)

    edited = patch(api, expense["id"], {"subtotal": None, "tax": None})

    assert (edited["subtotal"], edited["tax"]) == (None, None)


@pytest.mark.parametrize(
    ("answer_fields", "fix", "flag"),
    [
        ({"total": 9.99}, {"total": 2.49}, ("total", "sum_mismatch")),
        ({"subtotal": 9.99}, {"subtotal": None}, ("subtotal", "sum_mismatch")),
        ({"date": "2026-10-09"}, {"date": "2026-10-04"}, ("date", "date_in_future")),
        ({"date": "2023-01-01"}, {"date": "2026-10-04"}, ("date", "date_too_old")),
        ({"date": None}, {"date": "2026-10-04"}, ("date", "missing_date")),
        ({"merchant": None}, {"merchant": "Edeka"}, ("merchant", "missing_merchant")),
        ({"total": None, "line_items": TWO_BREADS}, {"total": 4.98}, ("total", "missing_total")),
    ],
)
def test_fixing_a_field_clears_its_flag(
    api: TestClient, model: ModelServer, answer_fields: dict, fix: dict, flag: tuple
) -> None:
    _, expense = extracted(api, model, **answer_fields)
    assert flag in codes(expense)

    edited = patch(api, expense["id"], fix)

    # Only the uncategorised items are left.
    assert {code for _, code in codes(edited)} == {"uncategorized_item"}
    assert edited["review_status"] == "needs_review"


def test_an_edit_can_raise_a_flag(api: TestClient) -> None:
    expense = create(api)

    edited = patch(api, expense["id"], {"total": 5.00})

    assert ("total", "sum_mismatch") in codes(edited)


def test_fixing_every_flag_makes_the_expense_accepted(api: TestClient, model: ModelServer) -> None:
    _, expense = extracted(api, model, total=9.99)

    edited = patch(api, expense["id"], {"total": 2.49, "line_items": categorised(expense)})

    assert (edited["review_status"], edited["flags"]) == ("accepted", [])


@pytest.mark.parametrize(
    ("origin", "edits", "source"),
    [("ai", 1, "ai_corrected"), ("ai", 2, "ai_corrected"), ("manual", 1, "manual")],
)
def test_patch_source(
    api: TestClient, model: ModelServer, origin: str, edits: int, source: str
) -> None:
    expense = extracted(api, model)[1] if origin == "ai" else create(api)
    assert expense["source"] == origin

    for n in range(edits):
        edited = patch(api, expense["id"], {"merchant": f"Edeka {n}"})

    assert edited["source"] == source


def test_an_empty_patch_changes_nothing(api: TestClient, model: ModelServer) -> None:
    _, expense = extracted(api, model)

    assert patch(api, expense["id"], {}) == expense


@pytest.mark.parametrize(
    ("keys", "edit", "left"),
    [
        (["tax", "subtotal"], {"tax": 0.16}, ["subtotal"]),
        (["tax", "subtotal"], {"subtotal": None}, ["tax"]),
        (["tax", "subtotal"], {"merchant": "Edeka"}, ["tax", "subtotal"]),
        (["line_items", "tax"], {"line_items": "resent"}, ["tax"]),
        (["tax", "line_items"], {"total": 2.49}, ["tax", "line_items"]),
        (["currency", "payment_method"], {"currency": "EUR"}, ["payment_method"]),
    ],
)
def test_an_edited_field_leaves_unreadable_fields(
    api: TestClient, model: ModelServer, keys: list, edit: dict, left: list
) -> None:
    _, expense = extracted(api, model, subtotal=None, tax=None, unreadable_fields=keys)
    assert unreadable(expense) == keys
    if edit.get("line_items") == "resent":
        edit = {"line_items": resent(expense)}

    edited = patch(api, expense["id"], edit)

    assert unreadable(edited) == left
    # A dropped key stays dropped on the next edit.
    assert unreadable(patch(api, expense["id"], {"merchant": "Edeka 2"})) == left


def test_line_items_are_updated_created_and_deleted(api: TestClient) -> None:
    expense = create(api)
    banane, cola = expense["line_items"]

    edited = patch(
        api,
        expense["id"],
        {
            "line_items": [
                {"description": "Wasser", "amount": 0.49},
                {"id": cola["id"], "description": "Cola", "amount": 1.79, "qty": 2},
            ]
        },
    )

    wasser, cola_after = edited["line_items"]
    assert wasser["id"] not in (banane["id"], cola["id"])
    assert (wasser["description"], wasser["amount"]) == ("Wasser", 0.49)
    assert cola_after == cola | {"amount": 1.79, "qty": 2}
    assert api.get(f"/api/expenses/{expense['id']}").json()["line_items"] == edited["line_items"]
    # The deleted item is gone: its id is no longer one of this expense's.
    response = api.patch(
        f"/api/expenses/{expense['id']}",
        json={"line_items": [{"id": banane["id"], "description": "x", "amount": 1}]},
    )
    assert response.status_code == 422


def test_items_not_sent_stay_as_they_are(api: TestClient) -> None:
    expense = create(api)

    edited = patch(api, expense["id"], {"total": 4.00})

    assert edited["line_items"] == expense["line_items"]


@pytest.mark.parametrize(
    ("keep_id", "description", "sent", "category", "source"),
    [
        # an existing item, unchanged description, no category: the stored one stays
        (True, "Cola", None, "alcohol", "user"),
        # a changed description: the categorizer decides
        (True, "Brot", None, "groceries.staples", "seed"),
        (True, "Cola Zero", None, "uncategorized", "none"),
        # a new item: the categorizer decides
        (False, "Cola", None, "drinks", "seed"),
        # category sent: the user's
        (True, "Cola", "other", "other", "user"),
        (False, "Cola", "other", "other", "user"),
    ],
)
def test_item_category_on_patch(
    api: TestClient,
    keep_id: bool,
    description: str,
    sent: str | None,
    category: str,
    source: str,
) -> None:
    # Stored before the categorizer knows `cola`, and different from what it would say.
    expense = create(
        api, line_items=[{"description": "Cola", "amount": 3.68, "category": "alcohol"}]
    )
    api.app.dependency_overrides[get_item_categorizer] = LookupCategorizer  # type: ignore[attr-defined]
    item: dict[str, Any] = {"description": description, "amount": 3.68}
    if keep_id:
        item["id"] = expense["line_items"][0]["id"]
    if sent is not None:
        item["category"] = sent

    edited = patch(api, expense["id"], {"line_items": [item]})

    [after] = edited["line_items"]
    assert (after["category"], after["category_source"]) == (category, source)
    assert after["normalized_name"] == description.lower()
    assert (after["id"] == expense["line_items"][0]["id"]) is keep_id


def test_an_uncategorised_item_resent_unchanged_stays_uncategorised(api: TestClient) -> None:
    expense = create(api, line_items=[{"description": "Cola", "amount": 3.68}])
    api.app.dependency_overrides[get_item_categorizer] = LookupCategorizer  # type: ignore[attr-defined]

    edited = patch(api, expense["id"], {"line_items": resent(expense, amount=4.00)})

    assert edited["line_items"][0]["category"] == "uncategorized"


def test_a_foreign_item_id_is_a_validation_error(api: TestClient) -> None:
    expense = create(api)
    other = create(api)
    own = expense["line_items"][0]["id"]
    foreign = other["line_items"][0]["id"]

    response = api.patch(
        f"/api/expenses/{expense['id']}",
        json={
            "merchant": "Edeka",
            "line_items": [
                {"id": own, "description": "Banane", "amount": 1.99},
                {"id": foreign, "description": "Cola", "amount": 1.69},
                {"id": 99999, "description": "Wasser", "amount": 0.49},
                {"id": own, "description": "Banane", "amount": 1.99},
            ],
        },
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": "validation_error",
        "detail": "The request is invalid.",
        "fields": [
            {
                "field": "line_items.1.id",
                "message": f"Item {foreign} is not an item of this expense.",
            },
            {"field": "line_items.2.id", "message": "Item 99999 is not an item of this expense."},
            {"field": "line_items.3.id", "message": f"Item {own} is listed twice."},
        ],
    }
    # Nothing changed, on either expense.
    assert api.get(f"/api/expenses/{expense['id']}").json() == expense
    assert api.get(f"/api/expenses/{other['id']}").json() == other


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"total": None}, "total"),
        ({"merchant": None}, "merchant"),
        ({"date": None}, "date"),
        ({"currency": None}, "currency"),
        ({"line_items": None}, "line_items"),
        ({"line_items": []}, "line_items"),
        ({"total": 10**10}, "total"),
        ({"total": -(10**10)}, "total"),
        ({"total": 1.999}, "total"),
        ({"line_items": [{"description": "X", "amount": 10**10}]}, "line_items.0.amount"),
        ({"source": "manual"}, "source"),
        ({"confirmed": True}, "confirmed"),
        ({"receipt_id": 1}, "receipt_id"),
    ],
)
def test_invalid_patch_is_422(api: TestClient, body: dict, field: str) -> None:
    expense = create(api)

    response = api.patch(f"/api/expenses/{expense['id']}", json=body)

    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"
    assert field in [f["field"] for f in response.json()["fields"]]
    assert api.get(f"/api/expenses/{expense['id']}").json() == expense


def test_the_largest_amount_is_accepted(api: TestClient) -> None:
    expense = create(api)

    edited = patch(api, expense["id"], {"total": 9999999999.99})

    assert edited["total"] == 9999999999.99


def test_patch_of_an_unknown_expense_is_404(api: TestClient) -> None:
    response = api.patch("/api/expenses/999", json={"merchant": "Edeka"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Expense 999 does not exist."


def test_patch_of_a_confirmed_expense_unconfirms_it(api: TestClient, model: ModelServer) -> None:
    receipt_id, expense = extracted(api, model)
    patch(api, expense["id"], {"line_items": categorised(expense)})
    assert confirm(api, expense["id"])["confirmed"] is True
    assert receipt_status(api, receipt_id) == "confirmed"

    edited = patch(api, expense["id"], {"merchant": "Edeka"})

    assert edited["confirmed"] is False
    assert receipt_status(api, receipt_id) == "extracted"
    # It can be confirmed again.
    assert confirm(api, expense["id"])["confirmed"] is True
    assert receipt_status(api, receipt_id) == "confirmed"


def test_a_failed_edit_leaves_the_receipt_confirmed(
    api: TestClient, model: ModelServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The receipt and the expense change in one transaction."""
    receipt_id, expense = extracted(api, model)
    patch(api, expense["id"], {"line_items": categorised(expense)})
    confirmed = confirm(api, expense["id"])

    def broken(*args: object, **kwargs: object) -> None:
        raise StorageError()

    monkeypatch.setattr(ExpenseRepository, "update", broken)

    response = api.patch(f"/api/expenses/{expense['id']}", json={"merchant": "Edeka"})

    assert response.status_code == 500
    assert receipt_status(api, receipt_id) == "confirmed"
    assert api.get(f"/api/expenses/{expense['id']}").json() == confirmed


# ---------------------------------------------------------------- confirm (F06)


def test_confirm_marks_expense_and_receipt_confirmed(api: TestClient, model: ModelServer) -> None:
    receipt_id, expense = extracted(api, model, total=9.99)
    ready = patch(api, expense["id"], {"line_items": categorised(expense)})
    assert ready["review_status"] == "needs_review"  # sum_mismatch: flags don't block

    confirmed = confirm(api, expense["id"])

    assert confirmed == ready | {"confirmed": True}
    assert receipt_status(api, receipt_id) == "confirmed"
    assert api.get(f"/api/receipts/{receipt_id}").json()["expense"] == confirmed
    assert api.get("/api/expenses", params={"confirmed": "true"}).json() == [confirmed]


def test_confirming_twice_is_a_no_op(api: TestClient, model: ModelServer) -> None:
    receipt_id, expense = extracted(api, model)
    patch(api, expense["id"], {"line_items": categorised(expense)})
    first = confirm(api, expense["id"])

    assert confirm(api, expense["id"]) == first
    assert receipt_status(api, receipt_id) == "confirmed"


def test_a_manual_expense_without_receipt_can_be_confirmed(api: TestClient) -> None:
    items = [{**item, "category": "groceries.fresh"} for item in MANUAL["line_items"]]
    expense = create(api, line_items=items)

    confirmed = confirm(api, expense["id"])

    assert (confirmed["confirmed"], confirmed["receipt_id"]) == (True, None)


def test_a_manual_expense_for_a_failed_receipt_can_be_confirmed(
    api: TestClient, model: ModelServer
) -> None:
    receipt_id = failed_receipt(api, model)
    items = [{**item, "category": "groceries.fresh"} for item in MANUAL["line_items"]]
    expense = create(api, receipt_id=receipt_id, line_items=items)

    confirm(api, expense["id"])

    assert receipt_status(api, receipt_id) == "confirmed"


@pytest.mark.parametrize(
    ("answer_fields", "edit", "missing"),
    [
        ({"merchant": None}, {}, "merchant"),
        ({}, {"merchant": "   "}, "merchant"),
        ({"date": None}, {}, "date"),
        ({"total": None, "line_items": TWO_BREADS}, {}, "total"),
        ({"merchant": None, "date": None}, {}, "merchant, date"),
    ],
)
def test_confirm_of_an_incomplete_expense_is_422(
    api: TestClient, model: ModelServer, answer_fields: dict, edit: dict, missing: str
) -> None:
    receipt_id, expense = extracted(api, model, **answer_fields)
    ready = patch(api, expense["id"], {"line_items": categorised(expense)} | edit)

    response = api.post(f"/api/expenses/{expense['id']}/confirm")

    assert response.status_code == 422
    assert response.json() == {
        "error": "incomplete_expense",
        "detail": (
            "Merchant, date and total are required before the expense can be confirmed; "
            f"missing: {missing}."
        ),
        "fields": None,
    }
    assert api.get(f"/api/expenses/{expense['id']}").json() == ready
    assert receipt_status(api, receipt_id) == "extracted"


@pytest.mark.parametrize(("uncategorised", "text"), [(1, "1 item has"), (2, "2 items have")])
def test_confirm_with_uncategorised_items_is_422(
    api: TestClient, uncategorised: int, text: str
) -> None:
    items = [{**item} for item in MANUAL["line_items"]]
    if uncategorised == 2:
        items[1].pop("category")
    expense = create(api, line_items=items)

    response = api.post(f"/api/expenses/{expense['id']}/confirm")

    assert response.status_code == 422
    assert response.json() == {
        "error": "uncategorized_items",
        "detail": f"Every item needs a category before the expense can be confirmed; {text} none.",
        "fields": None,
    }
    assert api.get(f"/api/expenses/{expense['id']}").json() == expense


def test_a_missing_field_is_reported_before_uncategorised_items(
    api: TestClient, model: ModelServer
) -> None:
    _, expense = extracted(api, model, date=None)

    response = api.post(f"/api/expenses/{expense['id']}/confirm")

    assert response.json()["error"] == "incomplete_expense"


def test_a_receipt_out_of_step_is_logged_and_left(
    api: TestClient, settings: Settings, model: ModelServer, caplog: pytest.LogCaptureFixture
) -> None:
    """Unreachable through the API; if it happens anyway, the expense still wins."""
    receipt_id, expense = extracted(api, model)
    patch(api, expense["id"], {"line_items": categorised(expense)})
    with database_for(settings.database_url).transaction() as session:
        assert ReceiptRepository(session).transition(receipt_id, "extracted", "failed")

    assert confirm(api, expense["id"])["confirmed"] is True

    assert receipt_status(api, receipt_id) == "failed"
    message = f"Expense {expense['id']}: receipt {receipt_id} was not extracted, left as it is"
    assert message in caplog.messages


def test_confirm_of_an_unknown_expense_is_404(api: TestClient) -> None:
    response = api.post("/api/expenses/999/confirm")

    assert response.status_code == 404
    assert response.json()["detail"] == "Expense 999 does not exist."


# ---------------------------------------------------------------- delete (F06)


def test_delete_with_receipt_removes_receipt_and_image(
    api: TestClient, settings: Settings, model: ModelServer
) -> None:
    receipt_id, expense = extracted(api, model)
    assert len(os.listdir(settings.upload_dir)) == 1

    response = api.delete(f"/api/expenses/{expense['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert api.get(f"/api/expenses/{expense['id']}").status_code == 404
    assert api.get(f"/api/receipts/{receipt_id}").status_code == 404
    assert api.get(f"/api/receipts/{receipt_id}/image").status_code == 404
    assert os.listdir(settings.upload_dir) == []
    assert count(settings, "receipts") == count(settings, "expenses") == 0
    assert count(settings, "line_items") == 0


def test_delete_of_a_confirmed_expense_removes_its_receipt(
    api: TestClient, model: ModelServer
) -> None:
    receipt_id, expense = extracted(api, model)
    patch(api, expense["id"], {"line_items": categorised(expense)})
    confirm(api, expense["id"])

    assert api.delete(f"/api/expenses/{expense['id']}").status_code == 204
    assert api.get(f"/api/receipts/{receipt_id}").status_code == 404


def test_delete_without_receipt_removes_only_the_expense(
    api: TestClient, settings: Settings, model: ModelServer
) -> None:
    receipt_id, kept = extracted(api, model)
    expense = create(api)

    assert api.delete(f"/api/expenses/{expense['id']}").status_code == 204

    assert api.get(f"/api/expenses/{expense['id']}").status_code == 404
    assert api.get(f"/api/expenses/{kept['id']}").json() == kept
    assert receipt_status(api, receipt_id) == "extracted"
    assert count(settings, "expenses") == 1
    assert count(settings, "line_items") == len(kept["line_items"])
    assert len(os.listdir(settings.upload_dir)) == 1


def test_delete_answers_204_when_the_image_file_cannot_be_removed(
    api: TestClient,
    model: ModelServer,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    receipt_id, expense = extracted(api, model)

    def broken(self: ImageStore, name: str) -> None:
        raise StorageError() from PermissionError("denied")

    monkeypatch.setattr(ImageStore, "remove", broken)

    assert api.delete(f"/api/expenses/{expense['id']}").status_code == 204
    assert api.get(f"/api/receipts/{receipt_id}").status_code == 404
    assert f"Receipt {receipt_id}: image file not removed (PermissionError)" in caplog.messages


def test_delete_of_an_unknown_expense_is_404(api: TestClient) -> None:
    response = api.delete("/api/expenses/999")

    assert response.status_code == 404
    assert response.json() == {
        "error": "not_found",
        "detail": "Expense 999 does not exist.",
        "fields": None,
    }

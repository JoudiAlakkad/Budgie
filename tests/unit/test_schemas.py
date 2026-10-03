"""Wire conventions of the DTOs (contracts/api-endpoints.md#conventions, decision 0016)."""

import datetime as dt
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError

from app.api import schemas
from app.api.schemas import (
    Budget,
    Category,
    Expense,
    ExpenseCreate,
    ExpenseUpdate,
    Goal,
    ItemCategory,
    ItemCategoryUpdate,
    LineItem,
    LineItemCategory,
    LineItemInput,
    Receipt,
    SpendingCategory,
)

DOMAIN_LOGIC_MD = Path(__file__).resolve().parents[2] / "docs/wiki/backend/domain-logic.md"
SPECIAL_MARKER = "plus the special categories"


def wiki_categories() -> tuple[list[str], list[str]]:
    """(spending, special) categories from the `### Categories` section of domain-logic.md.

    The section is one sentence: the spending categories in backticks, then
    "plus the special categories" and the special ones in backticks.
    """
    text = DOMAIN_LOGIC_MD.read_text(encoding="utf-8")
    match = re.search(r"^### Categories\n(.*?)(?=^#|\Z)", text, flags=re.MULTILINE | re.DOTALL)
    assert match, "domain-logic.md has no ### Categories section"
    sentence = match.group(1).strip().split("\n\n")[0]
    assert SPECIAL_MARKER in sentence, sentence
    spending, special = sentence.split(SPECIAL_MARKER, 1)
    return re.findall(r"`([^`]+)`", spending), re.findall(r"`([^`]+)`", special)


SPENDING_CATEGORIES, SPECIAL_CATEGORIES = wiki_categories()

ITEM = {"description": "BIO BANANE 1 KG", "amount": "1.99"}
EXPENSE = {"merchant": "REWE", "date": "2026-10-03", "total": "1.99", "line_items": [ITEM]}


def _expense(**overrides: object) -> Expense:
    values: dict[str, object] = {
        "id": 1,
        "receipt_id": None,
        "merchant": None,
        "date": None,
        "currency": "EUR",
        "total": None,
        "subtotal": None,
        "tax": None,
        "source": "ai",
        "review_status": "rejected",
        "confirmed": False,
        "flags": [],
        "line_items": [],
    }
    values.update(overrides)
    return Expense.model_validate(values)


def test_money_serialises_as_a_json_number() -> None:
    item = LineItemInput.model_validate(ITEM)

    assert item.amount == Decimal("1.99")
    assert json.loads(item.model_dump_json())["amount"] == 1.99
    assert '"amount":1.99' in item.model_dump_json()
    assert item.model_dump()["amount"] == Decimal("1.99")


def test_money_accepts_a_json_number() -> None:
    item = LineItemInput.model_validate_json('{"description": "x", "amount": 1.99}')

    assert item.amount == Decimal("1.99")


@pytest.mark.parametrize("amount", ["1.999", "0.001", "12345678901.00"])
def test_money_rejects_more_than_two_decimals_or_too_many_digits(amount: str) -> None:
    with pytest.raises(ValidationError):
        LineItemInput.model_validate({**ITEM, "amount": amount})


def test_money_json_schema_is_a_number() -> None:
    schema = LineItemInput.model_json_schema()

    assert schema["properties"]["amount"] == {"type": "number", "title": "Amount"}


@pytest.mark.parametrize(
    ("model", "valid"),
    [
        (LineItemInput, ITEM),
        (ExpenseCreate, EXPENSE),
        (ExpenseUpdate, {}),
        (ItemCategoryUpdate, {"category": "drinks"}),
        (Budget, {"category": "drinks", "monthly_limit": 10}),
        (Goal, {"target_amount": 500, "target_date": "2027-06-30"}),
    ],
)
def test_request_dtos_reject_unknown_fields(model: type[BaseModel], valid: dict) -> None:
    model.model_validate(valid)
    with pytest.raises(ValidationError, match="extra"):
        model.model_validate({**valid, "unexpected": 1})


def test_expense_create_needs_a_line_item() -> None:
    with pytest.raises(ValidationError):
        ExpenseCreate.model_validate({**EXPENSE, "line_items": []})


def test_expense_create_defaults_to_eur_and_checks_currency() -> None:
    assert ExpenseCreate.model_validate(EXPENSE).currency == "EUR"
    with pytest.raises(ValidationError):
        ExpenseCreate.model_validate({**EXPENSE, "currency": "eur"})


def test_expense_update_keeps_track_of_sent_fields() -> None:
    update = ExpenseUpdate.model_validate({"merchant": "Aldi", "tax": None})

    assert update.model_fields_set == {"merchant", "tax"}
    with pytest.raises(ValidationError):
        ExpenseUpdate.model_validate({"merchant": None})


@pytest.mark.parametrize("limit", [0, -1])
def test_budget_limit_must_be_positive(limit: int) -> None:
    with pytest.raises(ValidationError):
        Budget.model_validate({"category": "drinks", "monthly_limit": limit})


def test_budget_rejects_non_spending_category() -> None:
    with pytest.raises(ValidationError):
        Budget.model_validate({"category": "deposit", "monthly_limit": 1})


def test_response_dtos_dump_every_key_as_null() -> None:
    receipt = Receipt.model_validate(
        {
            "id": 1,
            "status": "uploaded",
            "uploaded_at": dt.datetime(2026, 10, 3, 12, 0, tzinfo=dt.UTC),
            "error": None,
            "error_detail": None,
            "expense": None,
        }
    )

    assert json.loads(receipt.model_dump_json()) == {
        "id": 1,
        "status": "uploaded",
        "uploaded_at": "2026-10-03T12:00:00Z",
        "error": None,
        "error_detail": None,
        "expense": None,
    }
    assert set(json.loads(_expense().model_dump_json())) == set(Expense.model_fields)


@pytest.mark.parametrize(
    "model",
    [
        schemas.Receipt,
        schemas.Expense,
        schemas.LineItem,
        schemas.Flag,
        schemas.ItemCategory,
        schemas.CategorySpend,
        schemas.GoalProgress,
        schemas.InsightsSummary,
        schemas.Leak,
        schemas.Health,
    ],
)
def test_response_dto_fields_have_no_default(model: type[BaseModel]) -> None:
    """A forgotten value is a server error, never a left-out key."""
    assert all(field.is_required() for field in model.model_fields.values())


def test_missing_response_key_is_an_error() -> None:
    with pytest.raises(ValidationError):
        LineItem.model_validate(
            {
                "id": 1,
                "description": "x",
                "normalized_name": "x",
                "amount": "1.00",
                "category": "uncategorized",
                "category_source": "none",
            }
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (dt.datetime(2026, 10, 3, 12, 0, tzinfo=dt.UTC), "2026-10-03T12:00:00Z"),
        (
            dt.datetime(2026, 10, 3, 14, 0, tzinfo=dt.timezone(dt.timedelta(hours=2))),
            "2026-10-03T12:00:00Z",
        ),
        (dt.datetime(2026, 10, 3, 12, 0), "2026-10-03T12:00:00Z"),
    ],
    ids=["utc", "converted", "naive-as-utc"],
)
def test_timestamps_serialise_in_utc_with_offset(value: dt.datetime, expected: str) -> None:
    item = ItemCategory(
        normalized_name="banane", category="groceries.fresh", source="seed", updated_at=value
    )

    assert json.loads(item.model_dump_json())["updated_at"] == expected
    assert item.updated_at.utcoffset() == dt.timedelta(0)


def test_category_literals_match_domain_logic() -> None:
    assert SPENDING_CATEGORIES and SPECIAL_CATEGORIES  # the parser found both lists
    assert list(get_args(SpendingCategory)) == SPENDING_CATEGORIES
    assert list(get_args(Category)) == [*SPENDING_CATEGORIES, *SPECIAL_CATEGORIES]
    assert list(get_args(LineItemCategory)) == [*get_args(Category), "uncategorized"]


def test_not_a_receipt_is_a_receipt_error_code() -> None:
    assert "not_a_receipt" in get_args(schemas.ReceiptErrorCode)


def test_expense_create_can_attach_to_a_receipt() -> None:
    assert ExpenseCreate.model_validate({**EXPENSE, "receipt_id": 7}).receipt_id == 7

"""`RESPONSE_SCHEMA` (sent to the model) and `ReceiptExtraction` (validates it) agree."""

import math
import types
from typing import Any, Union, get_args, get_origin

import pytest
from pydantic import BaseModel, ValidationError

from app.ai.schema import (
    MAX_LINE_ITEMS,
    RESPONSE_FORMAT,
    RESPONSE_SCHEMA,
    LineItem,
    ReceiptExtraction,
    UnreadableField,
)

ITEM_SCHEMA = RESPONSE_SCHEMA["properties"]["line_items"]["items"]


def _nullable(annotation: Any) -> bool:
    return get_origin(annotation) in (Union, types.UnionType) and type(None) in get_args(annotation)


def _schema_nullable(prop: dict) -> bool:
    kind = prop["type"]
    return isinstance(kind, list) and "null" in kind


@pytest.mark.parametrize(
    ("schema", "model"),
    [(RESPONSE_SCHEMA, ReceiptExtraction), (ITEM_SCHEMA, LineItem)],
    ids=["receipt", "line_item"],
)
def test_schema_matches_the_model(schema: dict, model: type[BaseModel]) -> None:
    fields = model.model_fields

    assert list(schema["properties"]) == list(fields)
    assert schema["required"] == list(fields)
    assert all(field.is_required() for field in fields.values())
    assert schema["additionalProperties"] is False
    assert schema["type"] == "object"
    for name, field in fields.items():
        assert _schema_nullable(schema["properties"][name]) == _nullable(field.annotation), name


def test_is_receipt_comes_first() -> None:
    assert next(iter(RESPONSE_SCHEMA["properties"])) == "is_receipt"


def test_unreadable_fields_enum_and_line_item_cap() -> None:
    props = RESPONSE_SCHEMA["properties"]

    assert props["unreadable_fields"]["items"]["enum"] == list(get_args(UnreadableField))
    assert set(get_args(UnreadableField)) <= set(props)
    assert props["line_items"]["maxItems"] == MAX_LINE_ITEMS == 100


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _keys(item)}
    return set()


def test_no_category_anywhere() -> None:
    assert not any("categor" in key for key in _keys(RESPONSE_SCHEMA))
    assert not any("categor" in name for name in ReceiptExtraction.model_fields)
    assert not any("categor" in name for name in LineItem.model_fields)


def test_response_format_wraps_the_schema() -> None:
    assert RESPONSE_FORMAT == {
        "type": "json_schema",
        "json_schema": {"name": "receipt_extraction", "schema": RESPONSE_SCHEMA},
    }


def valid() -> dict:
    return {
        "is_receipt": True,
        "merchant": "ALDI",
        "date": "2026-09-17",
        "currency": "EUR",
        "line_items": [{"description": "BROT", "qty": 1, "unit_price": 2.49, "amount": 2.49}],
        "subtotal": None,
        "tax": None,
        "total": 2.49,
        "unreadable_fields": [],
    }


def with_change(**changes: Any) -> dict:
    data = valid()
    data.update(changes)
    return data


def without(key: str) -> dict:
    data = valid()
    del data[key]
    return data


def item(**changes: Any) -> dict:
    return {"description": "BROT", "qty": 1, "unit_price": 2.49, "amount": 2.49, **changes}


REJECTED = {
    "missing merchant": without("merchant"),
    "missing unreadable_fields": without("unreadable_fields"),
    "missing item qty": with_change(line_items=[{"description": "X", "amount": 1.0}]),
    "101 items": with_change(line_items=[item()] * 101),
    "unknown unreadable field": with_change(unreadable_fields=["Summe"]),
    "NaN total": with_change(total=math.nan),
    "infinite amount": with_change(line_items=[item(amount=math.inf)]),
    "string amount": with_change(line_items=[item(amount="1,99")]),
    "null amount": with_change(line_items=[item(amount=None)]),
    "null line_items": with_change(line_items=None),
}

ACCEPTED = {
    "valid": valid(),
    "null merchant": with_change(merchant=None),
    "negative amount": with_change(line_items=[item(amount=-0.25, unit_price=-0.25)]),
    "100 items": with_change(line_items=[item()] * 100),
    "extra key ignored": with_change(category="food"),
    "unreadable merchant": with_change(merchant=None, unreadable_fields=["merchant"]),
}


@pytest.mark.parametrize("data", REJECTED.values(), ids=REJECTED.keys())
def test_rejected(data: dict) -> None:
    with pytest.raises(ValidationError):
        ReceiptExtraction.model_validate(data)


@pytest.mark.parametrize("data", ACCEPTED.values(), ids=ACCEPTED.keys())
def test_accepted(data: dict) -> None:
    extraction = ReceiptExtraction.model_validate(data)

    assert not hasattr(extraction, "category")

"""`RESPONSE_SCHEMA` is generated from `ReceiptExtraction`, flattened, and agrees with it."""

import json
import math
import types
from typing import Any, Literal, Union, get_args, get_origin

import pytest
from pydantic import BaseModel, Field, ValidationError

from app.ai.schema import (
    EXAMPLE_JSON,
    EXAMPLE_OUTPUT,
    MAX_LINE_ITEMS,
    MAX_UNREADABLE_FIELDS,
    RESPONSE_FORMAT,
    RESPONSE_SCHEMA,
    LineItem,
    PaymentMethod,
    ReceiptExtraction,
    UnreadableField,
    response_schema,
)

ITEM_SCHEMA = RESPONSE_SCHEMA["properties"]["line_items"]["items"]


def nullable(kind: str, description: str) -> dict:
    return {"type": [kind, "null"], "description": description}


# The schema the model sees, written out for reviewers. A change to the models that
# changes what the model is sent has to change this dict too.
EXPECTED_SCHEMA = {
    "type": "object",
    "properties": {
        "is_receipt": {
            "type": "boolean",
            "description": "false if the image is not a shop receipt",
        },
        "merchant": nullable("string", "the shop name only, no address or phone"),
        "date": nullable("string", "purchase date as YYYY-MM-DD"),
        "currency": nullable("string", "ISO 4217 code, e.g. EUR"),
        "line_items": {
            "type": "array",
            "description": "purchased products only, never payment or summary lines",
            "items": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "the product name exactly as printed",
                    },
                    "qty": nullable("number", "count or weight; null if not printed"),
                    "unit_price": nullable(
                        "number", "price per unit or per kg; null if not printed"
                    ),
                    "amount": {
                        "type": "number",
                        "description": "the line price; negative for discounts and deposit returns",
                    },
                },
                "required": ["description", "qty", "unit_price", "amount"],
                "additionalProperties": False,
            },
            "maxItems": 100,
        },
        "subtotal": nullable("number", "only if printed on the receipt, else null"),
        "tax": nullable("number", "only if printed on the receipt, else null"),
        "total": nullable(
            "number", "the amount paid (SUMME / ZU ZAHLEN), not a row of the tax table"
        ),
        "payment_method": {
            "type": ["string", "null"],
            "description": (
                "how it was paid: cash (BAR), card (EC, girocard, credit, contactless, "
                "mobile), voucher, other; never card numbers"
            ),
            "enum": ["cash", "card", "voucher", "other", None],
        },
        "unreadable_fields": {
            "type": "array",
            "description": "the keys whose value could not be read and is null",
            "items": {
                "type": "string",
                "enum": [
                    "merchant",
                    "date",
                    "currency",
                    "line_items",
                    "subtotal",
                    "tax",
                    "total",
                    "payment_method",
                ],
            },
            "maxItems": 8,
        },
    },
    "required": [
        "is_receipt",
        "merchant",
        "date",
        "currency",
        "line_items",
        "subtotal",
        "tax",
        "total",
        "payment_method",
        "unreadable_fields",
    ],
    "additionalProperties": False,
}


def test_generated_schema_is_the_golden_schema() -> None:
    assert RESPONSE_SCHEMA == EXPECTED_SCHEMA
    # Order matters too: the model writes the keys in this order.
    assert json.dumps(RESPONSE_SCHEMA) == json.dumps(EXPECTED_SCHEMA)


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for name, item in value.items():
            if name == "properties":
                for sub in item.values():
                    yield from _walk(sub)
            else:
                yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def test_schema_is_flat() -> None:
    nodes = list(_walk(RESPONSE_SCHEMA))
    for node in nodes:
        assert not {"$ref", "$defs", "anyOf", "oneOf", "allOf", "title"} & set(node), node
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
    assert "$ref" not in json.dumps(RESPONSE_SCHEMA)


def test_flattening_keeps_a_field_called_title_and_a_nullable_enum() -> None:
    class Sample(BaseModel):
        title: str = Field(description="d")
        kind: Literal["a", "b"] | None
        child: LineItem | None

    schema = response_schema(Sample)

    assert list(schema["properties"]) == ["title", "kind", "child"]
    assert schema["properties"]["title"] == {"type": "string", "description": "d"}
    assert schema["properties"]["kind"] == {"type": ["string", "null"], "enum": ["a", "b", None]}
    child = schema["properties"]["child"]
    assert child["type"] == ["object", "null"]
    assert child["properties"] == ITEM_SCHEMA["properties"]


def test_flattening_rejects_wider_unions() -> None:
    class Wide(BaseModel):
        value: int | str | None

    with pytest.raises(ValueError, match="X | None"):
        response_schema(Wide)


def test_flattening_rejects_a_nullable_without_a_type() -> None:
    class Untyped(BaseModel):
        value: Any | None

    with pytest.raises(ValueError, match="needs an X with a `type`"):
        response_schema(Untyped)


def test_flattening_turns_a_nullable_single_literal_into_an_enum_with_null() -> None:
    class Single(BaseModel):
        only: Literal["a"] | None

    assert response_schema(Single)["properties"]["only"] == {
        "type": ["string", "null"],
        "enum": ["a", None],
    }


def test_flattening_orders_a_ref_with_siblings() -> None:
    class Parent(BaseModel):
        child: LineItem = Field(description="the child")

    child = response_schema(Parent)["properties"]["child"]

    assert child["description"] == "the child"
    assert list(child) == ["type", "description", "properties", "required", "additionalProperties"]


# ---------------------------------------------------------------- example answer


def test_example_validates_and_round_trips() -> None:
    assert ReceiptExtraction.model_validate_json(EXAMPLE_JSON) == EXAMPLE_OUTPUT
    assert json.loads(EXAMPLE_JSON)["payment_method"] == "card"


def test_example_items_sum_to_its_total() -> None:
    amounts = [entry.amount for entry in EXAMPLE_OUTPUT.line_items]
    assert sum(amounts) == pytest.approx(EXAMPLE_OUTPUT.total)


def test_example_content() -> None:
    example = EXAMPLE_OUTPUT
    assert (example.is_receipt, example.merchant, example.currency) == (
        True,
        "Beispiel Markt",
        "EUR",
    )
    assert example.date and example.date.startswith("2026-")
    assert len(example.line_items) == 4
    assert any(e.qty == 2 and e.unit_price == 0.95 for e in example.line_items)
    assert any(e.description == "PFAND" and e.amount == -0.25 for e in example.line_items)
    assert example.unreadable_fields == []
    payment_words = ("SUMME", "BAR", "KARTE", "EC", "VISA", "ZAHLEN")
    for entry in example.line_items:
        assert not any(word in entry.description.upper().split() for word in payment_words)


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
    # every value key can be unreadable
    assert set(get_args(UnreadableField)) == set(props) - {"is_receipt", "unreadable_fields"}
    assert props["payment_method"]["enum"] == [*get_args(PaymentMethod), None]
    assert props["line_items"]["maxItems"] == MAX_LINE_ITEMS == 100
    assert props["unreadable_fields"]["maxItems"] == MAX_UNREADABLE_FIELDS == 8
    # no `uniqueItems`: untested with Ollama; `maxItems` bounds repeats instead
    assert "uniqueItems" not in json.dumps(RESPONSE_SCHEMA)


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
        "payment_method": "card",
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
    "9 unreadable fields": with_change(unreadable_fields=["total"] * 9),
    "NaN total": with_change(total=math.nan),
    "infinite amount": with_change(line_items=[item(amount=math.inf)]),
    "string amount": with_change(line_items=[item(amount="1,99")]),
    "null amount": with_change(line_items=[item(amount=None)]),
    "null line_items": with_change(line_items=None),
    "missing payment_method": without("payment_method"),
    "unknown payment_method": with_change(payment_method="EC-Karte"),
    "payment_method as card number": with_change(payment_method="****1234"),
}

ACCEPTED = {
    "valid": valid(),
    "null merchant": with_change(merchant=None),
    "negative amount": with_change(line_items=[item(amount=-0.25, unit_price=-0.25)]),
    "100 items": with_change(line_items=[item()] * 100),
    "extra key ignored": with_change(category="food"),
    "unreadable merchant": with_change(merchant=None, unreadable_fields=["merchant"]),
    **{
        f"payment {method}": with_change(payment_method=method)
        for method in get_args(PaymentMethod)
    },
    "null payment_method": with_change(payment_method=None),
    "unreadable payment_method": with_change(
        payment_method=None, unreadable_fields=["payment_method"]
    ),
}


@pytest.mark.parametrize("data", REJECTED.values(), ids=REJECTED.keys())
def test_rejected(data: dict) -> None:
    with pytest.raises(ValidationError):
        ReceiptExtraction.model_validate(data)


@pytest.mark.parametrize("data", ACCEPTED.values(), ids=ACCEPTED.keys())
def test_accepted(data: dict) -> None:
    extraction = ReceiptExtraction.model_validate(data)

    assert not hasattr(extraction, "category")

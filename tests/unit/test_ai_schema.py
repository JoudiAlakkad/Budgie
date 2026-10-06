"""`RESPONSE_SCHEMA` is generated from `ReceiptExtraction`, flattened, and agrees with it."""

import dataclasses
import hashlib
import json
import math
import types
from typing import Any, Literal, Union, get_args, get_origin

import pytest
from pydantic import BaseModel, Field, ValidationError

from app.ai.schema import (
    EXAMPLE_JSON,
    EXAMPLE_JSON_V1,
    EXAMPLE_OUTPUT,
    EXAMPLE_OUTPUT_V1,
    MAX_LINE_ITEMS,
    MAX_UNREADABLE_FIELDS,
    MAX_UNREADABLE_FIELDS_V1,
    OUTPUT_SPECS,
    RESPONSE_FORMAT,
    RESPONSE_FORMAT_V1,
    RESPONSE_SCHEMA,
    RESPONSE_SCHEMA_V1,
    LineItem,
    LineItemV1,
    OutputSpec,
    PaymentMethod,
    ReceiptExtraction,
    ReceiptExtractionV1,
    UnreadableField,
    UnreadableFieldV1,
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
        "date": nullable("string", "the purchase date exactly as printed, without the time"),
        "currency": nullable("string", "ISO 4217 code, e.g. EUR"),
        "line_items": {
            "type": "array",
            "description": "purchased products only, never payment or summary lines",
            "items": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "the item's name as printed",
                    },
                    "qty": nullable(
                        "number", "how many pieces, or the weight; null if not printed"
                    ),
                    "amount": {
                        "type": "number",
                        "description": (
                            "the total paid for this line, all pieces together; positive for "
                            "a Pfand charged, negative for discounts and Pfand returned "
                            "(Leergut)"
                        ),
                    },
                },
                "required": ["description", "qty", "amount"],
                "additionalProperties": False,
            },
            "maxItems": 100,
        },
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
                    "total",
                    "payment_method",
                ],
            },
            "maxItems": 6,
        },
    },
    "required": [
        "is_receipt",
        "merchant",
        "date",
        "currency",
        "line_items",
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


RETIRED_KEYS = ("unit_price", "subtotal", "tax")


def test_the_model_is_never_asked_for_unit_price_subtotal_or_tax() -> None:
    """Decision 0019: they leave the schema the model server enforces, enums included."""
    sent = json.dumps(RESPONSE_FORMAT)
    for key in RETIRED_KEYS:
        assert f'"{key}"' not in sent, key
    assert "unit_price" not in EXAMPLE_JSON
    assert not set(RETIRED_KEYS) & set(json.loads(EXAMPLE_JSON))


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
    # the date as printed, not reformatted (decision 0019)
    assert example.date == "14.03.26"
    by_name = {e.description: e for e in example.line_items}
    # a multi-piece line carries the line total, not the price of one piece
    assert (by_name["BROETCHEN"].qty, by_name["BROETCHEN"].amount) == (2, 1.9)
    # a Pfand charged is a cost, Leergut is money back
    assert by_name["PFAND"].amount == 0.25
    assert by_name["LEERGUT"].amount < 0
    assert "MINERALWASSER" in by_name
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
    assert props["unreadable_fields"]["maxItems"] == MAX_UNREADABLE_FIELDS == 6
    assert len(get_args(UnreadableField)) == MAX_UNREADABLE_FIELDS
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
        "line_items": [{"description": "BROT", "qty": 1, "amount": 2.49}],
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
    return {"description": "BROT", "qty": 1, "amount": 2.49, **changes}


REJECTED = {
    "missing merchant": without("merchant"),
    "missing unreadable_fields": without("unreadable_fields"),
    "missing item qty": with_change(line_items=[{"description": "X", "amount": 1.0}]),
    "101 items": with_change(line_items=[item()] * 101),
    "unknown unreadable field": with_change(unreadable_fields=["Summe"]),
    "7 unreadable fields": with_change(unreadable_fields=["total"] * 7),
    "unreadable field not a list": with_change(unreadable_fields="tax"),
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
    "negative amount": with_change(line_items=[item(amount=-0.25)]),
    "100 items": with_change(line_items=[item()] * 100),
    "extra key ignored": with_change(category="food"),
    # an answer to the old schema (prompt v1, the recorded fixtures), decision 0019
    "old v1 answer": with_change(
        line_items=[item(unit_price=2.49)],
        subtotal=2.49,
        tax=0.16,
        unreadable_fields=["tax", "subtotal", "total"],
    ),
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
    for key in RETIRED_KEYS:
        assert not hasattr(extraction, key)
        assert all(not hasattr(entry, key) for entry in extraction.line_items)
    assert not set(extraction.unreadable_fields) & set(RETIRED_KEYS)


def test_retired_unreadable_keys_are_dropped_and_the_rest_kept_in_order() -> None:
    data = with_change(unreadable_fields=["subtotal", "merchant", "tax", "date"], merchant=None)

    assert ReceiptExtraction.model_validate(data).unreadable_fields == ["merchant", "date"]


# ---------------------------------------------------------------- prompt v1 (frozen, F11)
# Restored from a4a32e8's tests: v1's schema and example are frozen as the F11 baseline.

EXPECTED_SCHEMA_V1 = {
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

EXPECTED_EXAMPLE_V1 = {
    "is_receipt": True,
    "merchant": "Beispiel Markt",
    "date": "2026-03-14",
    "currency": "EUR",
    "line_items": [
        {"description": "VOLLMILCH 3,5%", "qty": 1.0, "unit_price": 1.19, "amount": 1.19},
        {"description": "BROETCHEN", "qty": 2.0, "unit_price": 0.95, "amount": 1.9},
        {"description": "BANANEN", "qty": 1.0, "unit_price": 1.49, "amount": 1.49},
        {"description": "PFAND", "qty": 1.0, "unit_price": -0.25, "amount": -0.25},
    ],
    "subtotal": None,
    "tax": None,
    "total": 4.33,
    "payment_method": "card",
    "unreadable_fields": [],
}

# SHA-256 of what a4a32e8 produced: `json.dumps(RESPONSE_FORMAT)` and `EXAMPLE_JSON`.
A4A32E8_RESPONSE_FORMAT_SHA256 = "e308a99f8fc96ec60f7a271bb8a0ee37f3f58dbc38129d3d8e36b2a933049313"
A4A32E8_EXAMPLE_JSON_SHA256 = "3767c60cb217ebedee806b3e7fc521faaa7e9936fb518af890b44034d1772333"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_v1_generated_schema_is_the_golden_schema() -> None:
    assert RESPONSE_SCHEMA_V1 == EXPECTED_SCHEMA_V1
    assert json.dumps(RESPONSE_SCHEMA_V1) == json.dumps(EXPECTED_SCHEMA_V1)


def test_v1_response_format_is_byte_identical_to_a4a32e8() -> None:
    assert RESPONSE_FORMAT_V1 == {
        "type": "json_schema",
        "json_schema": {"name": "receipt_extraction", "schema": EXPECTED_SCHEMA_V1},
    }
    assert _sha256(json.dumps(RESPONSE_FORMAT_V1)) == A4A32E8_RESPONSE_FORMAT_SHA256


def test_v1_example_is_byte_identical_to_a4a32e8() -> None:
    assert json.dumps(EXPECTED_EXAMPLE_V1, indent=2, ensure_ascii=False) == EXAMPLE_JSON_V1
    assert _sha256(EXAMPLE_JSON_V1) == A4A32E8_EXAMPLE_JSON_SHA256


def test_v1_example_validates_and_sums_to_its_total() -> None:
    assert ReceiptExtractionV1.model_validate_json(EXAMPLE_JSON_V1) == EXAMPLE_OUTPUT_V1
    amounts = [entry.amount for entry in EXAMPLE_OUTPUT_V1.line_items]
    assert sum(amounts) == pytest.approx(EXAMPLE_OUTPUT_V1.total)


def test_v1_example_content() -> None:
    example = EXAMPLE_OUTPUT_V1
    assert example.date == "2026-03-14"
    assert len(example.line_items) == 4
    assert any(e.qty == 2 and e.unit_price == 0.95 for e in example.line_items)
    assert any(e.description == "PFAND" and e.amount == -0.25 for e in example.line_items)


@pytest.mark.parametrize(
    ("schema", "model"),
    [
        (RESPONSE_SCHEMA_V1, ReceiptExtractionV1),
        (RESPONSE_SCHEMA_V1["properties"]["line_items"]["items"], LineItemV1),
    ],
    ids=["receipt", "line_item"],
)
def test_v1_schema_matches_the_model(schema: dict, model: type[BaseModel]) -> None:
    fields = model.model_fields

    assert list(schema["properties"]) == list(fields)
    assert schema["required"] == list(fields)
    for name, field in fields.items():
        assert _schema_nullable(schema["properties"][name]) == _nullable(field.annotation), name


def test_v1_unreadable_fields() -> None:
    props = RESPONSE_SCHEMA_V1["properties"]

    assert props["unreadable_fields"]["items"]["enum"] == list(get_args(UnreadableFieldV1))
    assert set(get_args(UnreadableFieldV1)) == set(props) - {"is_receipt", "unreadable_fields"}
    assert props["unreadable_fields"]["maxItems"] == MAX_UNREADABLE_FIELDS_V1 == 8


def test_v1_keeps_unit_price_subtotal_and_tax_and_v2_does_not() -> None:
    v1, v2 = OUTPUT_SPECS["v1"], OUTPUT_SPECS["v2"]
    for key in RETIRED_KEYS:
        assert f'"{key}"' in json.dumps(v1.response_format), key
        assert f'"{key}"' in v1.example_json, key
        assert f'"{key}"' not in json.dumps(v2.response_format), key
        assert f'"{key}"' not in v2.example_json, key
    assert set(RETIRED_KEYS) <= set(ReceiptExtractionV1.model_fields) | set(LineItemV1.model_fields)


def test_output_specs() -> None:
    assert OUTPUT_SPECS["v1"] == OutputSpec(
        ReceiptExtractionV1, RESPONSE_FORMAT_V1, EXAMPLE_JSON_V1
    )
    assert OUTPUT_SPECS["v2"] == OutputSpec(ReceiptExtraction, RESPONSE_FORMAT, EXAMPLE_JSON)
    with pytest.raises(dataclasses.FrozenInstanceError):
        OUTPUT_SPECS["v1"].example_json = "{}"  # type: ignore[misc]


def test_v1_model_rejects_a_v2_answer() -> None:
    """A lean v2 answer lacks v1's required keys."""
    with pytest.raises(ValidationError) as raised:
        ReceiptExtractionV1.model_validate(valid())
    missing = {".".join(map(str, e["loc"])) for e in raised.value.errors()}
    assert missing == {"subtotal", "tax", "line_items.0.unit_price"}


def test_v1_model_keeps_subtotal_and_tax_as_unreadable_fields() -> None:
    data = {
        **valid(),
        "line_items": [item(unit_price=2.49)],
        "subtotal": None,
        "tax": None,
        "unreadable_fields": ["subtotal", "tax"],
    }
    assert ReceiptExtractionV1.model_validate(data).unreadable_fields == ["subtotal", "tax"]

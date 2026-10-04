"""The model's output schema (docs/wiki/backend/ai-extraction.md).

`ReceiptExtraction` validates the answer; `RESPONSE_SCHEMA` is the JSON schema sent
as `response_format`. Both are kept in sync by tests/unit/test_ai_schema.py.

From the AI spike: every key is required and nullable (with optional keys the model
left out merchant, date and total), `unreadable_fields` is an enum of schema keys
(free text looped until the token limit), and `line_items` is capped. There is no
category field: categorisation is deterministic (decision 0013).
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_LINE_ITEMS = 100

UnreadableField = Literal["merchant", "date", "currency", "line_items", "subtotal", "tax", "total"]


class _Output(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class LineItem(_Output):
    description: str
    qty: float | None
    unit_price: float | None
    amount: float


class ReceiptExtraction(_Output):
    is_receipt: bool
    merchant: str | None
    date: str | None
    currency: str | None
    line_items: list[LineItem] = Field(max_length=MAX_LINE_ITEMS)
    subtotal: float | None
    tax: float | None
    total: float | None
    unreadable_fields: list[UnreadableField]


_LINE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "qty": {"type": ["number", "null"]},
        "unit_price": {"type": ["number", "null"]},
        "amount": {"type": "number"},
    },
    "required": ["description", "qty", "unit_price", "amount"],
    "additionalProperties": False,
}

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_receipt": {"type": "boolean"},
        "merchant": {"type": ["string", "null"]},
        "date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "currency": {"type": ["string", "null"], "description": "ISO 4217, e.g. EUR"},
        "line_items": {
            "type": "array",
            "items": _LINE_ITEM_SCHEMA,
            "maxItems": MAX_LINE_ITEMS,
        },
        "subtotal": {"type": ["number", "null"]},
        "tax": {"type": ["number", "null"]},
        "total": {"type": ["number", "null"]},
        "unreadable_fields": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["merchant", "date", "currency", "line_items", "subtotal", "tax", "total"],
            },
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
        "unreadable_fields",
    ],
    "additionalProperties": False,
}

RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {"name": "receipt_extraction", "schema": RESPONSE_SCHEMA},
}

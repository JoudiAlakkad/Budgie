"""The model's output schema (docs/wiki/backend/ai-extraction.md).

The Pydantic models are the single source: `ReceiptExtraction` validates the answer,
and `RESPONSE_SCHEMA`, the JSON schema sent as `response_format`, is generated from it
by `response_schema`. The field descriptions go into that schema, so the model sees them.

From the AI spike: every key is required and nullable (with optional keys the model
left out merchant, date and total), `unreadable_fields` is an enum of schema keys
(free text looped until the token limit), and `line_items` is capped. There is no
category field: categorisation is deterministic (decision 0013).

The generated schema is flattened to the form the spike tested on Ollama: no `$defs`
or `$ref`, no `anyOf` (a nullable value is `type: [X, "null"]`), no `title`, and
`additionalProperties: false` on every object.
"""

from copy import deepcopy
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_LINE_ITEMS = 100

PaymentMethod = Literal["cash", "card", "voucher", "other"]
UnreadableField = Literal[
    "merchant", "date", "currency", "line_items", "subtotal", "tax", "total", "payment_method"
]


class _Output(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class LineItem(_Output):
    description: str = Field(description="the product name exactly as printed")
    qty: float | None = Field(description="count or weight; null if not printed")
    unit_price: float | None = Field(description="price per unit or per kg; null if not printed")
    amount: float = Field(description="the line price; negative for discounts and deposit returns")


class ReceiptExtraction(_Output):
    # Key order drives the generated schema, and so the order the model writes in.
    is_receipt: bool = Field(description="false if the image is not a shop receipt")
    merchant: str | None = Field(description="the shop name only, no address or phone")
    date: str | None = Field(description="purchase date as YYYY-MM-DD")
    currency: str | None = Field(description="ISO 4217 code, e.g. EUR")
    line_items: list[LineItem] = Field(
        max_length=MAX_LINE_ITEMS,
        description="purchased products only, never payment or summary lines",
    )
    subtotal: float | None = Field(description="only if printed on the receipt, else null")
    tax: float | None = Field(description="only if printed on the receipt, else null")
    total: float | None = Field(
        description="the amount paid (SUMME / ZU ZAHLEN), not a row of the tax table"
    )
    payment_method: PaymentMethod | None = Field(
        description=(
            "how it was paid: cash (BAR), card (EC, girocard, credit, contactless, mobile), "
            "voucher, other; never card numbers"
        )
    )
    unreadable_fields: list[UnreadableField] = Field(
        description="the keys whose value could not be read and is null"
    )


def _resolve(node: Any, defs: dict[str, Any]) -> Any:
    """Inline `$ref`s, collapse nullable `anyOf`s and drop titles, recursively."""
    if isinstance(node, list):
        return [_resolve(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        target = _resolve(deepcopy(defs[node["$ref"].rsplit("/", 1)[-1]]), defs)
        # Keywords next to the `$ref` (e.g. a description) win over the target's.
        rest = {key: value for key, value in node.items() if key != "$ref"}
        return {**target, **_resolve(rest, defs)}
    node = {key: value for key, value in node.items() if key not in ("title", "$defs")}
    if "anyOf" in node:
        node = _collapse_nullable(node, defs)
    result = {
        # `properties` maps field names to schemas: a field may be called `title`.
        key: {name: _resolve(sub, defs) for name, sub in value.items()}
        if key == "properties"
        else _resolve(value, defs)
        for key, value in node.items()
    }
    if result.get("type") == "object":
        result["additionalProperties"] = False
    # A fixed keyword order keeps the schema readable; property order is never touched.
    rank = {key: i for i, key in enumerate(_KEYWORD_ORDER)}
    return dict(sorted(result.items(), key=lambda kv: rank.get(kv[0], len(rank))))


_KEYWORD_ORDER = (
    "type",
    "description",
    "enum",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "maxItems",
)


def _collapse_nullable(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """`anyOf: [X, {type: null}]` -> X with `null` added to its type (and enum)."""
    options = node.pop("anyOf")
    others = [option for option in options if option != {"type": "null"}]
    if len(others) != 1 or len(options) != 2:
        raise ValueError(f"only `X | None` unions are supported, got {options}")
    inner = _resolve(others[0], defs)
    kind = inner["type"]
    inner["type"] = [*kind, "null"] if isinstance(kind, list) else [kind, "null"]
    if "enum" in inner:
        inner["enum"] = [*inner["enum"], None]
    return {**inner, **node}


def response_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The flattened JSON schema of `model`, in the form sent to the model server."""
    raw = model.model_json_schema()
    return _resolve(raw, raw.get("$defs", {}))


RESPONSE_SCHEMA: dict[str, Any] = response_schema(ReceiptExtraction)

RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {"name": "receipt_extraction", "schema": RESPONSE_SCHEMA},
}

# A synthetic answer for the system prompt: it shows the shape, never values to copy.
# Built as a model, so it is validated when the module loads.
EXAMPLE_OUTPUT = ReceiptExtraction(
    is_receipt=True,
    merchant="Beispiel Markt",
    date=date(2026, 3, 14).isoformat(),
    currency="EUR",
    line_items=[
        LineItem(description="VOLLMILCH 3,5%", qty=1, unit_price=1.19, amount=1.19),
        # from the two lines "BROETCHEN" and "2 x 0,95"
        LineItem(description="BROETCHEN", qty=2, unit_price=0.95, amount=1.9),
        LineItem(description="BANANEN", qty=1, unit_price=1.49, amount=1.49),
        LineItem(description="PFAND", qty=1, unit_price=-0.25, amount=-0.25),
    ],
    subtotal=None,
    tax=None,
    total=4.33,
    payment_method="card",
    unreadable_fields=[],
)
EXAMPLE_JSON = EXAMPLE_OUTPUT.model_dump_json(indent=2)

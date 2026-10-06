"""The model's output schema (docs/wiki/backend/ai-extraction.md).

The Pydantic models are the single source: `ReceiptExtraction` validates the answer,
and `RESPONSE_SCHEMA`, the JSON schema sent as `response_format`, is generated from it
by `response_schema`. The field descriptions go into that schema, so the model sees them.

From the AI spike: every key is required and nullable (with optional keys the model
left out merchant, date and total), `unreadable_fields` is an enum of schema keys
(free text looped until the token limit), and `line_items` is capped. There is no
category field: categorisation is deterministic (decision 0013).

The generated schema is flattened: no `$defs` or `$ref`, no `anyOf` (a nullable value
is `type: [X, "null"]`), no `title`. The spike's strict schema on Ollama used only
type arrays and required nullable keys. The nullable enum (`payment_method`),
`additionalProperties: false` on every object and `maxItems` are new in F03; the live
integration test passed with them against Ollama 0.35.1 on 2026-10-04. `uniqueItems`
stays out (never tried); `maxItems` on `unreadable_fields` bounds repeats instead.
"""

from copy import deepcopy
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_LINE_ITEMS = 100
PaymentMethod = Literal["cash", "card", "voucher", "other"]
UnreadableField = Literal["merchant", "date", "currency", "line_items", "total", "payment_method"]
# One per value key; stops a model repeating enum values.
MAX_UNREADABLE_FIELDS = len(get_args(UnreadableField))
# Keys removed by decision 0019. An answer to the old schema (prompt v1, the recorded
# fixtures) may still list them in `unreadable_fields`; they are dropped, like the
# removed keys themselves are ignored, so that answer still parses.
RETIRED_UNREADABLE_FIELDS = frozenset({"subtotal", "tax"})


class _Output(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class LineItem(_Output):
    # Decision 0019: name, qty and the line total only; no unit price.
    description: str = Field(description="the item's name as printed")
    qty: float | None = Field(description="how many pieces, or the weight; null if not printed")
    amount: float = Field(
        description=(
            "the total paid for this line, all pieces together; positive for a Pfand charged, "
            "negative for discounts and Pfand returned (Leergut)"
        )
    )


class ReceiptExtraction(_Output):
    # Key order drives the generated schema, and so the order the model writes in.
    is_receipt: bool = Field(description="false if the image is not a shop receipt")
    merchant: str | None = Field(description="the shop name only, no address or phone")
    date: str | None = Field(description="the purchase date exactly as printed, without the time")
    currency: str | None = Field(description="ISO 4217 code, e.g. EUR")
    line_items: list[LineItem] = Field(
        max_length=MAX_LINE_ITEMS,
        description="purchased products only, never payment or summary lines",
    )
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
        max_length=MAX_UNREADABLE_FIELDS,
        description="the keys whose value could not be read and is null",
    )

    @field_validator("unreadable_fields", mode="before")
    @classmethod
    def _drop_retired_keys(cls, value: Any) -> Any:
        """Drop `subtotal` and `tax` (decision 0019); anything else is validated as usual."""
        if isinstance(value, list):
            return [key for key in value if key not in RETIRED_UNREADABLE_FIELDS]
        return value


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
        return _ordered({**target, **_resolve(rest, defs)})
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
    return _ordered(result)


def _ordered(node: dict[str, Any]) -> dict[str, Any]:
    """A fixed keyword order keeps the schema readable; property order is never touched."""
    rank = {key: i for i, key in enumerate(_KEYWORD_ORDER)}
    return dict(sorted(node.items(), key=lambda kv: rank.get(kv[0], len(rank))))


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
    """`anyOf: [X, {type: null}]` -> X with `null` added to its type (and enum).

    A single-value `Literal["a"] | None` (`const: "a"`) becomes `enum: ["a", null]`.
    Raises `ValueError` for wider unions and for an X without a `type`.
    """
    options = node.pop("anyOf")
    others = [option for option in options if option != {"type": "null"}]
    if len(others) != 1 or len(options) != 2:
        raise ValueError(f"only `X | None` unions are supported, got {options}")
    inner = _resolve(others[0], defs)
    kind = inner.get("type")
    if kind is None:
        raise ValueError(f"`X | None` needs an X with a `type`, got {others[0]}")
    inner["type"] = [*kind, "null"] if isinstance(kind, list) else [kind, "null"]
    if "const" in inner:
        inner["enum"] = [inner.pop("const")]
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
# Built as a model, so it is validated when the module loads. The date is as printed
# (decision 0019); `domain.validation.parse_date` reads it.
EXAMPLE_OUTPUT = ReceiptExtraction(
    is_receipt=True,
    merchant="Beispiel Markt",
    date="14.03.26",
    currency="EUR",
    line_items=[
        LineItem(description="VOLLMILCH 3,5%", qty=1, amount=1.19),
        # one block of two lines, "2 x 0,95" and "BROETCHEN": the amount is its total
        LineItem(description="BROETCHEN", qty=2, amount=1.9),
        LineItem(description="MINERALWASSER", qty=1, amount=0.49),
        # the deposit charged for the bottle: a cost
        LineItem(description="PFAND", qty=1, amount=0.25),
        # bottles brought back: money given back
        LineItem(description="LEERGUT", qty=None, amount=-0.75),
    ],
    total=3.08,
    payment_method="card",
    unreadable_fields=[],
)
EXAMPLE_JSON = EXAMPLE_OUTPUT.model_dump_json(indent=2)

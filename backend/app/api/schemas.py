"""Public DTOs: the API contract (docs/wiki/contracts/api-endpoints.md).

Conventions (decision 0016):
- Money is a `Decimal` on the server and a JSON number with at most 2 decimals on the wire.
- Dates are `YYYY-MM-DD`; timestamps are ISO 8601 in UTC with an offset.
- Response DTOs always contain every key: nullable fields have no default, so a missing
  value is an explicit `None` and is dumped as `null`.
- Request DTOs forbid unknown fields (`422 validation_error`).
- `api` doesn't import `domain`, so the category list from domain-logic.md is mirrored here.
"""

import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
)

# ---------------------------------------------------------------- scalar types

Money = Annotated[
    Decimal,
    Field(max_digits=12, decimal_places=2),
    PlainSerializer(float, return_type=float, when_used="json"),
    WithJsonSchema({"type": "number"}),
]
"""An amount: `Decimal` in Python, a JSON number with at most 2 decimals on the wire."""

PositiveMoney = Annotated[
    Decimal,
    Field(max_digits=12, decimal_places=2, gt=0),
    PlainSerializer(float, return_type=float, when_used="json"),
    WithJsonSchema({"type": "number", "exclusiveMinimum": 0}),
]
"""Money that must be greater than 0 (`Budget.monthly_limit`)."""

Quantity = Annotated[
    Decimal,
    PlainSerializer(float, return_type=float, when_used="json"),
    WithJsonSchema({"type": "number"}),
]
"""A line item quantity, e.g. `1.234` (kg). Not money, so no 2-decimal limit."""

Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$", examples=["EUR"])]

Month = Annotated[str, Field(pattern=r"^\d{4}-\d{2}$", examples=["2026-10"])]


def _to_utc(value: dt.datetime) -> dt.datetime:
    # Naive values (SQLite drops the zone) are taken as UTC; aware ones are converted.
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


Timestamp = Annotated[dt.datetime, AfterValidator(_to_utc)]
"""A UTC timestamp; always serialised with its offset."""

# ---------------------------------------------------------------- enumerations

SpendingCategory = Literal[
    "groceries.fresh",
    "groceries.staples",
    "snacks_sweets",
    "drinks",
    "alcohol",
    "tobacco",
    "household",
    "personal_care",
    "health",
    "eating_out",
    "transport",
    "clothing",
    "electronics",
    "other",
]
"""The categories that count as spending (domain-logic.md#categories)."""

Category = Literal[
    "groceries.fresh",
    "groceries.staples",
    "snacks_sweets",
    "drinks",
    "alcohol",
    "tobacco",
    "household",
    "personal_care",
    "health",
    "eating_out",
    "transport",
    "clothing",
    "electronics",
    "other",
    "deposit",
    "discount",
]
"""`SpendingCategory` plus the special categories `deposit` and `discount`."""

LineItemCategory = Literal[
    "groceries.fresh",
    "groceries.staples",
    "snacks_sweets",
    "drinks",
    "alcohol",
    "tobacco",
    "household",
    "personal_care",
    "health",
    "eating_out",
    "transport",
    "clothing",
    "electronics",
    "other",
    "deposit",
    "discount",
    "uncategorized",
]
"""A line item's category: a `Category`, or `uncategorized`."""

ReceiptStatus = Literal["uploaded", "extracting", "extracted", "failed", "confirmed"]

ReceiptErrorCode = Literal[
    "llm_unavailable",
    "llm_timeout",
    "llm_error",
    "malformed_output",
    "not_a_receipt",
    "unreadable_image",
    "interrupted",
]
"""Stored on a `failed` receipt (error-format.md#receipt-error-codes); not HTTP errors."""

ErrorCode = Literal[
    "bad_request",
    "not_found",
    "method_not_allowed",
    "invalid_state",
    "file_too_large",
    "unsupported_file",
    "uncategorized_items",
    "incomplete_expense",
    "validation_error",
    "storage_error",
    "internal_error",
    "not_implemented",
]
"""The `error` of an HTTP error body (error-format.md#http-error-codes)."""

ReviewStatus = Literal["accepted", "needs_review", "rejected"]
Source = Literal["ai", "ai_corrected", "manual"]
CategorySource = Literal["seed", "user", "none"]
ItemCategorySource = Literal["seed", "user"]
SpendState = Literal["under", "on_pace_to_overrun", "over"]
LeakType = Literal["recurring", "over_budget", "on_pace_to_overrun", "spike", "small_frequent"]


class _Request(BaseModel):
    """Base for request bodies: unknown fields are a validation error."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- errors


class FieldError(BaseModel):
    """One failed field of a `validation_error`."""

    field: str = Field(examples=["line_items.0.amount"])
    message: str


class ErrorBody(BaseModel):
    """The body of every non-2xx response (error-format.md)."""

    error: ErrorCode
    detail: str
    fields: list[FieldError] | None = None


# ---------------------------------------------------------------- health


class Health(BaseModel):
    """`GET /api/health`. Always returned with 200 while the app runs."""

    status: Literal["ok"]
    db: Literal["ok", "error"]
    llm: Literal["ok", "down"]
    model: str


# ---------------------------------------------------------------- receipts and expenses


class Flag(BaseModel):
    """A rule result on an expense, e.g. `sum_mismatch` on `total`."""

    field: str | None
    code: str
    message: str


class LineItem(BaseModel):
    id: int
    description: str
    normalized_name: str
    qty: Quantity | None
    unit: str | None
    unit_price: Money | None
    amount: Money
    category: LineItemCategory
    category_source: CategorySource


class Expense(BaseModel):
    id: int
    receipt_id: int | None
    merchant: str | None
    date: dt.date | None
    currency: Currency
    total: Money | None
    subtotal: Money | None
    tax: Money | None
    source: Source
    review_status: ReviewStatus
    confirmed: bool
    flags: list[Flag]
    line_items: list[LineItem]


class Receipt(BaseModel):
    id: int
    status: ReceiptStatus
    uploaded_at: Timestamp
    error: ReceiptErrorCode | None
    error_detail: str | None
    expense: Expense | None


class LineItemInput(_Request):
    """A line item as sent by the client; the server derives `normalized_name`."""

    id: int | None = None
    description: str
    qty: Quantity | None = None
    unit: str | None = None
    unit_price: Money | None = None
    amount: Money
    category: Category | None = None


class ExpenseCreate(_Request):
    """`POST /expenses`: a manual expense, optionally attached to a failed receipt."""

    receipt_id: int | None = None
    merchant: str
    date: dt.date
    currency: Currency = "EUR"
    total: Money
    subtotal: Money | None = None
    tax: Money | None = None
    line_items: list[LineItemInput] = Field(min_length=1)


class ExpenseUpdate(_Request):
    """`PATCH /expenses/{id}`: only the fields sent change.

    Fields that are required in `ExpenseCreate` may be left out but not sent as `null`.
    `line_items`, if sent, replaces the list.
    """

    merchant: str = None  # type: ignore[assignment]
    date: dt.date = None  # type: ignore[assignment]
    currency: Currency = None  # type: ignore[assignment]
    total: Money = None  # type: ignore[assignment]
    subtotal: Money | None = None
    tax: Money | None = None
    line_items: list[LineItemInput] = Field(default=None, min_length=1)  # type: ignore[assignment]


# ---------------------------------------------------------------- settings and insights


class ItemCategory(BaseModel):
    """One entry of the item lookup table."""

    normalized_name: str
    category: Category
    source: ItemCategorySource
    updated_at: Timestamp


class ItemCategoryUpdate(_Request):
    """`PUT /item-categories/{normalized_name}`."""

    category: Category


class Budget(_Request):
    """A monthly limit for one spending category. Sent and returned in `/budgets`."""

    category: SpendingCategory
    monthly_limit: PositiveMoney


class Goal(_Request):
    """The savings goal. Sent and returned in `/goal`; `monthly_income` is always dumped."""

    target_amount: Money
    target_date: dt.date
    monthly_income: Money | None = None


class CategorySpend(BaseModel):
    category: SpendingCategory
    spent: Money
    budget: Money | None
    projected: Money
    state: SpendState


class GoalProgress(BaseModel):
    target_amount: Money
    target_date: dt.date
    saved_this_month: Money | None
    required_per_month: Money
    on_track: bool | None


class InsightsSummary(BaseModel):
    month: Month
    total_spent: Money
    total_budget: Money | None
    projected_total: Money
    categories: list[CategorySpend]
    goal: GoalProgress | None


class Leak(BaseModel):
    type: LeakType
    category: SpendingCategory | None
    merchant: str | None
    amount: Money
    explanation: str

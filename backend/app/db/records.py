"""Frozen records the repositories return and take; never ORM objects (persistence.md)."""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ReceiptRecord:
    id: int
    image_path: str
    image_type: str
    status: str
    error: str | None
    uploaded_at: dt.datetime
    model_name: str | None
    prompt_version: str | None
    latency_ms: int | None
    raw_model_output: str | None


@dataclass(frozen=True)
class FlagRecord:
    field: str | None
    code: str
    message: str


@dataclass(frozen=True)
class LineItemRecord:
    id: int
    position: int
    description: str
    normalized_name: str
    qty: Decimal | None
    unit: str | None
    unit_price: Decimal | None
    amount: Decimal
    category: str
    category_source: str


@dataclass(frozen=True)
class ExpenseRecord:
    id: int
    receipt_id: int | None
    merchant: str | None
    date: dt.date | None
    currency: str
    subtotal: Decimal | None
    tax: Decimal | None
    total: Decimal | None
    source: str
    review_status: str
    confirmed: bool
    flags: tuple[FlagRecord, ...]
    unreadable_fields: tuple[str, ...]
    created_at: dt.datetime
    updated_at: dt.datetime
    line_items: tuple[LineItemRecord, ...]


@dataclass(frozen=True)
class NewLineItem:
    """A line item to insert; `position` comes from its index in `NewExpense.line_items`."""

    description: str
    normalized_name: str
    qty: Decimal | None
    unit: str | None
    unit_price: Decimal | None
    amount: Decimal
    category: str
    category_source: str


@dataclass(frozen=True)
class NewExpense:
    """An expense to insert, with its line items in order."""

    receipt_id: int | None
    merchant: str | None
    date: dt.date | None
    currency: str
    subtotal: Decimal | None
    tax: Decimal | None
    total: Decimal | None
    source: str
    review_status: str
    flags: tuple[FlagRecord, ...]
    unreadable_fields: tuple[str, ...]
    line_items: tuple[NewLineItem, ...]
    confirmed: bool = False


@dataclass(frozen=True)
class ExpenseFilter:
    """`GET /expenses` filters; `None` means no filter."""

    review_status: str | None = None
    confirmed: bool | None = None
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    category: str | None = None

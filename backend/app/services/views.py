"""Plain views the services return; field names match the DTOs in `app.api.schemas`.

A router builds its DTO with `Model.model_validate(view, from_attributes=True)`
(modules.md). Money is rounded to 0.01 here, so a response never fails validation.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from app.db.records import ExpenseRecord, LineItemRecord, ReceiptRecord
from app.domain.money import cents


def optional_cents(value: Decimal | None) -> Decimal | None:
    return None if value is None else cents(value)


@dataclass(frozen=True)
class FlagView:
    field: str | None
    code: str
    message: str


@dataclass(frozen=True)
class LineItemView:
    id: int
    description: str
    normalized_name: str
    qty: Decimal | None
    unit: str | None
    unit_price: Decimal | None
    amount: Decimal
    category: str
    category_source: str


@dataclass(frozen=True)
class ExpenseView:
    id: int
    receipt_id: int | None
    merchant: str | None
    date: dt.date | None
    currency: str
    total: Decimal | None
    subtotal: Decimal | None
    tax: Decimal | None
    source: str
    review_status: str
    confirmed: bool
    flags: list[FlagView]
    line_items: list[LineItemView]
    created_at: dt.datetime


@dataclass(frozen=True)
class ReceiptView:
    id: int
    status: str
    uploaded_at: dt.datetime
    error: str | None
    error_detail: str | None
    expense: ExpenseView | None


@dataclass(frozen=True)
class ItemCategoryView:
    normalized_name: str
    category: str
    source: str
    updated_at: dt.datetime


@dataclass(frozen=True)
class ImageView:
    data: bytes
    media_type: str


@dataclass(frozen=True)
class BudgetView:
    category: str
    monthly_limit: Decimal


@dataclass(frozen=True)
class GoalView:
    target_amount: Decimal
    target_date: dt.date
    monthly_income: Decimal | None


@dataclass(frozen=True)
class CategorySpendView:
    category: str
    spent: Decimal
    budget: Decimal | None
    projected: Decimal
    state: str


@dataclass(frozen=True)
class GoalProgressView:
    target_amount: Decimal
    target_date: dt.date
    saved_this_month: Decimal | None
    required_per_month: Decimal
    on_track: bool | None


@dataclass(frozen=True)
class InsightsSummaryView:
    month: str
    total_spent: Decimal
    total_budget: Decimal | None
    projected_total: Decimal
    categories: list[CategorySpendView]
    goal: GoalProgressView | None


@dataclass(frozen=True)
class LeakView:
    type: str
    category: str | None
    merchant: str | None
    amount: Decimal
    explanation: str


def line_item_view(item: LineItemRecord) -> LineItemView:
    return LineItemView(
        id=item.id,
        description=item.description,
        normalized_name=item.normalized_name,
        qty=item.qty,
        unit=item.unit,
        unit_price=optional_cents(item.unit_price),
        amount=cents(item.amount),
        category=item.category,
        category_source=item.category_source,
    )


def expense_view(expense: ExpenseRecord) -> ExpenseView:
    return ExpenseView(
        id=expense.id,
        receipt_id=expense.receipt_id,
        merchant=expense.merchant,
        date=expense.date,
        currency=expense.currency,
        total=optional_cents(expense.total),
        subtotal=optional_cents(expense.subtotal),
        tax=optional_cents(expense.tax),
        source=expense.source,
        review_status=expense.review_status,
        confirmed=expense.confirmed,
        flags=[FlagView(flag.field, flag.code, flag.message) for flag in expense.flags],
        line_items=[line_item_view(item) for item in expense.line_items],
        created_at=expense.created_at,
    )


def receipt_view(
    receipt: ReceiptRecord, expense: ExpenseRecord | None, error_detail: str | None
) -> ReceiptView:
    return ReceiptView(
        id=receipt.id,
        status=receipt.status,
        uploaded_at=receipt.uploaded_at,
        error=receipt.error,
        error_detail=error_detail,
        expense=expense_view(expense) if expense is not None else None,
    )

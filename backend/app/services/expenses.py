"""Expense use cases built in F05: list, get and manual create (contracts/api-endpoints.md).

Editing, confirming and deleting follow in F06.
"""

import datetime as dt
import logging
from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol

from app.db.records import ExpenseFilter, FlagRecord, NewExpense, NewLineItem
from app.db.repositories.expenses import ExpenseRepository
from app.db.repositories.receipts import ReceiptRepository
from app.db.session import Database
from app.domain.confidence import assess
from app.domain.facts import ItemFacts, ReceiptFacts
from app.errors import InvalidState, NotFound
from app.services.categorization import ItemCategorizer
from app.services.receipt_pipeline import Today
from app.services.views import ExpenseView, cents, expense_view, optional_cents

logger = logging.getLogger(__name__)


class LineItemInput(Protocol):
    """A line item as sent by the client (`api.schemas.LineItemInput`)."""

    @property
    def description(self) -> str: ...
    @property
    def qty(self) -> Decimal | None: ...
    @property
    def unit(self) -> str | None: ...
    @property
    def unit_price(self) -> Decimal | None: ...
    @property
    def amount(self) -> Decimal: ...
    @property
    def category(self) -> str | None: ...


class ExpenseInput(Protocol):
    """A manual expense as sent by the client (`api.schemas.ExpenseCreate`)."""

    @property
    def receipt_id(self) -> int | None: ...
    @property
    def merchant(self) -> str: ...
    @property
    def date(self) -> dt.date: ...
    @property
    def currency(self) -> str: ...
    @property
    def total(self) -> Decimal: ...
    @property
    def subtotal(self) -> Decimal | None: ...
    @property
    def tax(self) -> Decimal | None: ...
    @property
    def line_items(self) -> Sequence[LineItemInput]: ...


class ExpenseService:
    def __init__(self, db: Database, categorizer: ItemCategorizer, today: Today) -> None:
        self._db = db
        self._categorizer = categorizer
        self._today = today

    def list(
        self,
        *,
        review_status: str | None = None,
        confirmed: bool | None = None,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
        category: str | None = None,
    ) -> list[ExpenseView]:
        """Newest date first, expenses without a date last, ties by id descending.

        `category` matches expenses with at least one item in it; the dates are inclusive.
        """
        filters = ExpenseFilter(review_status, confirmed, date_from, date_to, category)
        with self._db.transaction() as session:
            expenses = ExpenseRepository(session).list(filters)
        return [expense_view(expense) for expense in expenses]

    def get(self, expense_id: int) -> ExpenseView:
        with self._db.transaction() as session:
            expense = ExpenseRepository(session).get(expense_id)
        if expense is None:
            raise NotFound(f"Expense {expense_id} does not exist.")
        return expense_view(expense)

    def create(self, body: ExpenseInput) -> ExpenseView:
        """A hand-typed expense with `source: manual`, assessed by the rules.

        With `receipt_id`, the receipt goes `failed` -> `extracted` in the same transaction
        (decision 0015): `NotFound` for an unknown receipt, `InvalidState` unless it is
        failed. The merchant is stored as typed; a category sent on an item is stored with
        `category_source: user`.
        """
        items = [self._line_item(item) for item in body.line_items]
        facts = ReceiptFacts(
            merchant=body.merchant,
            date=body.date.isoformat(),
            currency=body.currency,
            subtotal=body.subtotal,
            tax=body.tax,
            total=body.total,
            items=tuple(ItemFacts(item.description, item.amount, item.category) for item in items),
        )
        review_status, flags = assess(facts, self._today())
        new = NewExpense(
            receipt_id=body.receipt_id,
            merchant=body.merchant,
            date=body.date,
            currency=body.currency,
            subtotal=optional_cents(body.subtotal),
            tax=optional_cents(body.tax),
            total=cents(body.total),
            source="manual",
            review_status=review_status,
            flags=tuple(FlagRecord(f.field, f.code, f.message) for f in flags),
            unreadable_fields=(),
            line_items=tuple(items),
        )
        with self._db.transaction() as session:
            if body.receipt_id is not None:
                self._attach(ReceiptRepository(session), body.receipt_id)
            expense = ExpenseRepository(session).insert(new)
        logger.info(
            "Expense %d created by hand, %d items, receipt %s",
            expense.id,
            len(expense.line_items),
            body.receipt_id,
        )
        return expense_view(expense)

    def _line_item(self, item: LineItemInput) -> NewLineItem:
        categorized = self._categorizer.categorize(item.description)
        if item.category is not None:
            category, source = item.category, "user"
        else:
            category, source = categorized.category, categorized.category_source
        return NewLineItem(
            description=item.description,
            normalized_name=categorized.normalized_name,
            qty=item.qty if item.qty is not None else categorized.qty,
            unit=item.unit if item.unit is not None else categorized.unit,
            unit_price=optional_cents(item.unit_price),
            amount=cents(item.amount),
            category=category,
            category_source=source,
        )

    @staticmethod
    def _attach(receipts: ReceiptRepository, receipt_id: int) -> None:
        """Enter manually: the failed receipt becomes `extracted`."""
        current = receipts.get(receipt_id)
        if current is None:
            raise NotFound(f"Receipt {receipt_id} does not exist.")
        if not receipts.transition(receipt_id, "failed", "extracted", error=None):
            raise InvalidState(
                f"Receipt {receipt_id} is {current.status}; an expense can be entered by hand "
                "only for a failed receipt."
            )

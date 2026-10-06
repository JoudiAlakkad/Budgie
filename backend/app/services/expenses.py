"""Expense use cases (contracts/api-endpoints.md#expenses): list, get and manual create
(F05); edit, confirm and delete (F06, decision 0018)."""

import dataclasses
import datetime as dt
import logging
from collections.abc import Sequence, Set
from decimal import Decimal
from typing import Any, Protocol

from app.db.images import ImageStore
from app.db.records import (
    ExpenseChanges,
    ExpenseFilter,
    ExpenseRecord,
    FlagRecord,
    LineItemChange,
    LineItemRecord,
    NewExpense,
    NewLineItem,
)
from app.db.repositories.expenses import ExpenseRepository
from app.db.repositories.receipts import ReceiptRepository
from app.db.session import Database
from app.domain.confidence import assess
from app.domain.facts import ItemFacts, ReceiptFacts, is_blank
from app.errors import (
    IncompleteExpense,
    InvalidFields,
    InvalidState,
    NotFound,
    UncategorizedItems,
)
from app.services.categorization import ItemCategorizer
from app.services.receipt_pipeline import Today
from app.services.receipts import remove_image
from app.services.views import ExpenseView, cents, expense_view, optional_cents

logger = logging.getLogger(__name__)

# The scalar fields a PATCH may change; `line_items` is handled on its own.
EDITABLE_SCALARS = ("merchant", "date", "currency", "subtotal", "tax", "total")


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


class EditedLineItem(LineItemInput, Protocol):
    """A line item in a PATCH: `id` names one of the expense's items, None adds one."""

    @property
    def id(self) -> int | None: ...


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


class ExpenseEdit(Protocol):
    """An edit as sent by the client (`api.schemas.ExpenseUpdate`).

    Only the fields in `model_fields_set` were sent; the others must be ignored.
    """

    @property
    def model_fields_set(self) -> Set[str]: ...
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
    def line_items(self) -> Sequence[EditedLineItem]: ...


class ExpenseService:
    def __init__(
        self, db: Database, categorizer: ItemCategorizer, today: Today, images: ImageStore
    ) -> None:
        self._db = db
        self._categorizer = categorizer
        self._today = today
        self._images = images

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
        review_status, flags = self._assess(
            merchant=body.merchant,
            date=body.date,
            currency=body.currency,
            subtotal=body.subtotal,
            tax=body.tax,
            total=body.total,
            items=items,
            unreadable_fields=(),
        )
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
            flags=flags,
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

    def update(self, expense_id: int, body: ExpenseEdit) -> ExpenseView:
        """Apply the fields sent, then reassess (decision 0018).

        - `source` `ai` becomes `ai_corrected`; `ai_corrected` and `manual` stay.
        - Each field sent leaves `unreadable_fields` (`line_items` when items are sent).
        - `line_items`, if sent, replaces the list; an id that isn't one of this
          expense's items, or is repeated, is `InvalidFields` on `line_items.<i>.id`.
        - A confirmed expense becomes unconfirmed, and its receipt goes back from
          `confirmed` to `extracted`, in the same transaction.

        A body with no fields is not an edit: it returns the expense unchanged.
        """
        sent = set(body.model_fields_set)
        with self._db.transaction() as session:
            expenses = ExpenseRepository(session)
            current = expenses.get(expense_id)
            if current is None:
                raise NotFound(f"Expense {expense_id} does not exist.")
            if not sent:
                return expense_view(current)
            values: dict[str, Any] = {
                name: getattr(body, name) if name in sent else getattr(current, name)
                for name in EDITABLE_SCALARS
            }
            if "line_items" in sent:
                items = self._edited_items(current, body.line_items)
            else:
                items = [_unchanged(item) for item in current.line_items]
            edited = sent & {*EDITABLE_SCALARS, "line_items"}
            unreadable = tuple(key for key in current.unreadable_fields if key not in edited)
            review_status, flags = self._assess(**values, items=items, unreadable_fields=unreadable)
            changes = ExpenseChanges(
                merchant=values["merchant"],
                date=values["date"],
                currency=values["currency"],
                subtotal=optional_cents(values["subtotal"]),
                tax=optional_cents(values["tax"]),
                total=optional_cents(values["total"]),
                source="ai_corrected" if current.source == "ai" else current.source,
                review_status=review_status,
                flags=flags,
                unreadable_fields=unreadable,
                confirmed=False,
                line_items=tuple(items),
            )
            if current.confirmed and current.receipt_id is not None:
                _move_receipt(ReceiptRepository(session), current, "confirmed", "extracted")
            expense = expenses.update(expense_id, changes)
        # `edited` holds field names only: ExpenseUpdate forbids unknown keys.
        logger.info(
            "Expense %d edited (%s), unconfirmed: %s",
            expense_id,
            ", ".join(sorted(edited)),
            current.confirmed,
        )
        return expense_view(expense)

    def confirm(self, expense_id: int) -> ExpenseView:
        """Confirm a complete, fully categorised expense; its receipt becomes `confirmed`.

        Flags don't block it. Confirming twice returns the expense unchanged. Raises
        `IncompleteExpense` while merchant (blank counts as missing), date or total is
        missing, then `UncategorizedItems` while any item is uncategorised.
        """
        with self._db.transaction() as session:
            expenses = ExpenseRepository(session)
            current = expenses.get(expense_id)
            if current is None:
                raise NotFound(f"Expense {expense_id} does not exist.")
            if current.confirmed:
                return expense_view(current)
            _check_confirmable(current)
            # A retry committed between the read and this write deletes the expense.
            if not expenses.set_confirmed(expense_id, True):
                raise NotFound(f"Expense {expense_id} does not exist.")
            if current.receipt_id is not None:
                _move_receipt(ReceiptRepository(session), current, "extracted", "confirmed")
            expense = expenses.get(expense_id)
        if expense is None:
            raise NotFound(f"Expense {expense_id} does not exist.")
        logger.info("Expense %d confirmed", expense_id)
        return expense_view(expense)

    def delete(self, expense_id: int) -> None:
        """Delete the expense. With a receipt, the receipt goes as `DELETE /receipts/{id}`
        does: the rows first (the cascade takes the expense), then the image file, whose
        removal failure is only logged. Without one, only the expense rows go."""
        with self._db.transaction() as session:
            current = ExpenseRepository(session).get(expense_id)
            if current is None:
                raise NotFound(f"Expense {expense_id} does not exist.")
            receipt = None
            if current.receipt_id is not None:
                receipt = ReceiptRepository(session).delete(current.receipt_id)
            if receipt is None:
                ExpenseRepository(session).delete(expense_id)
        if receipt is not None:
            remove_image(self._images, receipt.id, receipt.image_path)
        logger.info("Expense %d deleted, receipt %s", expense_id, current.receipt_id)

    def _assess(
        self,
        *,
        merchant: str | None,
        date: dt.date | None,
        currency: str,
        subtotal: Decimal | None,
        tax: Decimal | None,
        total: Decimal | None,
        items: Sequence[NewLineItem],
        unreadable_fields: tuple[str, ...],
    ) -> tuple[str, tuple[FlagRecord, ...]]:
        """The review status and flags from the rules; run on every create and edit."""
        facts = ReceiptFacts(
            merchant=merchant,
            date=date.isoformat() if date is not None else None,
            currency=currency,
            subtotal=subtotal,
            tax=tax,
            total=total,
            items=tuple(ItemFacts(item.description, item.amount, item.category) for item in items),
            unreadable_fields=unreadable_fields,
        )
        review_status, flags = assess(facts, self._today())
        return review_status, tuple(FlagRecord(f.field, f.code, f.message) for f in flags)

    def _edited_items(
        self, current: ExpenseRecord, sent: Sequence[EditedLineItem]
    ) -> list[LineItemChange]:
        """The sent items, categorised per decision 0018, after checking their ids."""
        stored = {item.id: item for item in current.line_items}
        problems: list[tuple[str, str]] = []
        seen: set[int] = set()
        for index, item in enumerate(sent):
            if item.id is None:
                continue
            if item.id not in stored:
                message = f"Item {item.id} is not an item of this expense."
                problems.append((f"line_items.{index}.id", message))
            elif item.id in seen:
                problems.append((f"line_items.{index}.id", f"Item {item.id} is listed twice."))
            seen.add(item.id)
        if problems:
            raise InvalidFields(problems)
        return [
            self._edited_item(item, stored[item.id] if item.id is not None else None)
            for item in sent
        ]

    def _edited_item(self, item: EditedLineItem, stored: LineItemRecord | None) -> LineItemChange:
        """`category` sent: the user's. An existing item with an unchanged description and
        no `category` keeps its stored one. Otherwise the categorizer decides, as on create."""
        new = self._line_item(item)
        if item.category is None and stored is not None and item.description == stored.description:
            new = dataclasses.replace(
                new, category=stored.category, category_source=stored.category_source
            )
        return LineItemChange(id=item.id, **_item_fields(new))

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
        """Enter manually: the failed receipt becomes `extracted`.

        Like retry, it clears the failed attempt: `error`, `model_name`, `prompt_version`,
        `latency_ms` and `raw_model_output`.
        """
        current = receipts.get(receipt_id)
        if current is None:
            raise NotFound(f"Receipt {receipt_id} does not exist.")
        if not receipts.transition(
            receipt_id,
            "failed",
            "extracted",
            error=None,
            model_name=None,
            prompt_version=None,
            latency_ms=None,
            raw_model_output=None,
        ):
            raise InvalidState(
                f"Receipt {receipt_id} is {current.status}; an expense can be entered by hand "
                "only for a failed receipt."
            )


def _item_fields(item: NewLineItem) -> dict[str, Any]:
    """The `NewLineItem` fields by name (shallow; `dataclasses.asdict` would deep-copy)."""
    return {field.name: getattr(item, field.name) for field in dataclasses.fields(NewLineItem)}


def _unchanged(item: LineItemRecord) -> LineItemChange:
    """A stored item, kept as it is when a PATCH doesn't send `line_items`."""
    return LineItemChange(
        id=item.id,
        description=item.description,
        normalized_name=item.normalized_name,
        qty=item.qty,
        unit=item.unit,
        unit_price=item.unit_price,
        amount=item.amount,
        category=item.category,
        category_source=item.category_source,
    )


def _move_receipt(receipts: ReceiptRepository, expense: ExpenseRecord, from_: str, to: str) -> None:
    """Keep the receipt's status in step with `confirmed` (receipt-lifecycle.md)."""
    assert expense.receipt_id is not None
    if not receipts.transition(expense.receipt_id, from_, to):
        # Only reachable if the two were already out of step; the expense still changes.
        logger.warning(
            "Expense %d: receipt %d was not %s, left as it is",
            expense.id,
            expense.receipt_id,
            from_,
        )


def _check_confirmable(expense: ExpenseRecord) -> None:
    """Merchant, date and total set, then every item categorised (receipt-lifecycle.md)."""
    missing = [
        name
        for name, absent in (
            ("merchant", is_blank(expense.merchant)),
            ("date", expense.date is None),
            ("total", expense.total is None),
        )
        if absent
    ]
    if missing:
        raise IncompleteExpense(
            "Merchant, date and total are required before the expense can be confirmed; "
            f"missing: {', '.join(missing)}."
        )
    uncategorized = sum(
        not ItemFacts(item.description, item.amount, item.category).is_categorized
        for item in expense.line_items
    )
    if uncategorized:
        none = "item has" if uncategorized == 1 else "items have"
        raise UncategorizedItems(
            "Every item needs a category before the expense can be confirmed; "
            f"{uncategorized} {none} none."
        )

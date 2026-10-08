"""Expense rows with their line items."""

import datetime as dt
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session, selectinload

from app.db.models import ExpenseRow, LineItemRow
from app.db.records import (
    DuplicateCandidate,
    ExpenseChanges,
    ExpenseFilter,
    ExpenseRecord,
    FlagRecord,
    LineItemChange,
    LineItemRecord,
    NewExpense,
    NewLineItem,
)
from app.errors import NotFound

# The duplicate rule's tolerance on the total (decision 0020); the domain decides the rest.
_CENT = Decimal("0.01")


def _now() -> dt.datetime:
    """Naive UTC, as stored (persistence.md)."""
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def _item_record(row: LineItemRow) -> LineItemRecord:
    return LineItemRecord(
        id=row.id,
        position=row.position,
        description=row.description,
        normalized_name=row.normalized_name,
        qty=row.qty,
        unit=row.unit,
        unit_price=row.unit_price,
        amount=row.amount,
        category=row.category,
        category_source=row.category_source,
    )


def to_record(row: ExpenseRow) -> ExpenseRecord:
    return ExpenseRecord(
        id=row.id,
        receipt_id=row.receipt_id,
        merchant=row.merchant,
        date=row.date,
        currency=row.currency,
        subtotal=row.subtotal,
        tax=row.tax,
        total=row.total,
        source=row.source,
        review_status=row.review_status,
        confirmed=row.confirmed,
        flags=tuple(
            FlagRecord(field=flag.get("field"), code=flag["code"], message=flag["message"])
            for flag in row.flags or []
        ),
        unreadable_fields=tuple(row.unreadable_fields or []),
        created_at=row.created_at,
        updated_at=row.updated_at,
        line_items=tuple(_item_record(item) for item in row.line_items),
    )


def _flags_json(flags: Iterable[FlagRecord]) -> list[dict[str, str | None]]:
    return [{"field": flag.field, "code": flag.code, "message": flag.message} for flag in flags]


def _write_item(row: LineItemRow, position: int, item: NewLineItem) -> LineItemRow:
    row.position = position
    row.description = item.description
    row.normalized_name = item.normalized_name
    row.qty = item.qty
    row.unit = item.unit
    row.unit_price = item.unit_price
    row.amount = item.amount
    row.category = item.category
    row.category_source = item.category_source
    return row


def _with_items() -> Select[tuple[ExpenseRow]]:
    return select(ExpenseRow).options(selectinload(ExpenseRow.line_items))


class ExpenseRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert(self, new: NewExpense) -> ExpenseRecord:
        now = _now()
        row = ExpenseRow(
            receipt_id=new.receipt_id,
            merchant=new.merchant,
            date=new.date,
            currency=new.currency,
            subtotal=new.subtotal,
            tax=new.tax,
            total=new.total,
            source=new.source,
            review_status=new.review_status,
            confirmed=new.confirmed,
            flags=_flags_json(new.flags),
            unreadable_fields=list(new.unreadable_fields),
            created_at=now,
            updated_at=now,
            line_items=[
                _write_item(LineItemRow(), position, item)
                for position, item in enumerate(new.line_items)
            ],
        )
        self._session.add(row)
        return self._reloaded(row)

    def update(self, expense_id: int, changes: ExpenseChanges) -> ExpenseRecord:
        """Write the merged state of an edited expense; `updated_at` becomes now.

        Items with an id are updated in place, items without one are inserted, stored
        items that aren't listed are deleted; `position` is the index in the list. Raises
        `NotFound` for an unknown expense and `ValueError` for an item id that isn't one
        of its items (the service checks both first).
        """
        row = self._session.scalars(_with_items().where(ExpenseRow.id == expense_id)).first()
        if row is None:
            raise NotFound(f"Expense {expense_id} does not exist.")
        stored = {item.id: item for item in row.line_items}
        items: list[LineItemRow] = []
        for position, change in enumerate(changes.line_items):
            items.append(_write_item(self._item_row(stored, change), position, change))
        row.merchant = changes.merchant
        row.date = changes.date
        row.currency = changes.currency
        row.subtotal = changes.subtotal
        row.tax = changes.tax
        row.total = changes.total
        row.source = changes.source
        row.review_status = changes.review_status
        row.confirmed = changes.confirmed
        row.flags = _flags_json(changes.flags)
        row.unreadable_fields = list(changes.unreadable_fields)
        row.updated_at = _now()
        row.line_items = items  # delete-orphan removes the items left out
        return self._reloaded(row)

    @staticmethod
    def _item_row(stored: dict[int, LineItemRow], change: LineItemChange) -> LineItemRow:
        if change.id is None:
            return LineItemRow()
        row = stored.pop(change.id, None)
        if row is None:
            raise ValueError(f"line item {change.id} is not an item of this expense, or repeated")
        return row

    def set_confirmed(self, expense_id: int, confirmed: bool) -> bool:
        """Set `confirmed` and `updated_at`; True if the expense exists."""
        statement = (
            update(ExpenseRow)
            .where(ExpenseRow.id == expense_id)
            .values(confirmed=confirmed, updated_at=_now())
        )
        result = self._session.execute(statement)
        # The bulk update bypasses the identity map; a later get must see the new value.
        self._session.expire_all()
        return result.rowcount == 1  # type: ignore[attr-defined]

    def delete(self, expense_id: int) -> ExpenseRecord | None:
        """Delete the expense and its items (not its receipt); the deleted record, or None."""
        row = self._session.scalars(_with_items().where(ExpenseRow.id == expense_id)).first()
        if row is None:
            return None
        record = to_record(row)
        self._session.delete(row)
        self._session.flush()
        return record

    def _reloaded(self, row: ExpenseRow) -> ExpenseRecord:
        self._session.flush()
        # Reload, so the record holds what the column types made of the values
        # (e.g. money rounded to cents), exactly as a later read would.
        self._session.expire(row)
        return to_record(self._session.scalars(_with_items().where(ExpenseRow.id == row.id)).one())

    def get(self, expense_id: int) -> ExpenseRecord | None:
        row = self._session.scalars(_with_items().where(ExpenseRow.id == expense_id)).first()
        return to_record(row) if row is not None else None

    def get_by_receipt(self, receipt_id: int) -> ExpenseRecord | None:
        query = _with_items().where(ExpenseRow.receipt_id == receipt_id)
        row = self._session.scalars(query).first()
        return to_record(row) if row is not None else None

    def by_receipts(self, receipt_ids: Iterable[int]) -> dict[int, ExpenseRecord]:
        """The expenses of these receipts, by receipt id, in one query."""
        ids = list(receipt_ids)
        if not ids:
            return {}
        rows = self._session.scalars(_with_items().where(ExpenseRow.receipt_id.in_(ids)))
        return {row.receipt_id: to_record(row) for row in rows if row.receipt_id is not None}

    def duplicate_candidates(
        self, date: dt.date, total: Decimal, exclude_id: int | None = None
    ) -> tuple[DuplicateCandidate, ...]:
        """Expenses on `date` whose total is within a cent of `total`, by id, without
        `exclude_id`; confirmed and drafts alike. The merchant is compared by the domain.

        A tuple, not `list[...]`: this class has a method named `list`, and Python 3.12
        evaluates annotations eagerly, so the result type mustn't depend on method order.
        """
        total = total.quantize(_CENT, rounding=ROUND_HALF_UP)  # as stored
        query = (
            select(ExpenseRow.id, ExpenseRow.merchant, ExpenseRow.date, ExpenseRow.total)
            .where(
                ExpenseRow.date == date,
                ExpenseRow.total.between(total - _CENT, total + _CENT),
            )
            .order_by(ExpenseRow.id)
        )
        if exclude_id is not None:
            query = query.where(ExpenseRow.id != exclude_id)
        return tuple(
            DuplicateCandidate(id=id_, merchant=merchant, date=day, total=amount)
            for id_, merchant, day, amount in self._session.execute(query)
        )

    def confirmed_spend_by_category(
        self, date_from: dt.date, date_to: dt.date
    ) -> dict[str, Decimal]:
        """The line-item amounts of confirmed expenses dated `date_from`..`date_to`
        (inclusive), summed per item category in one `SUM ... GROUP BY` query.

        Every category is returned, `deposit`, `discount` and `uncategorized` too; the
        domain decides what counts as spend (decision 0021).
        """
        total = func.sum(LineItemRow.amount).label("total")  # MoneyCents: a Decimal sum
        query = (
            select(LineItemRow.category, total)
            .join(ExpenseRow, LineItemRow.expense_id == ExpenseRow.id)
            .where(
                ExpenseRow.confirmed.is_(True),
                ExpenseRow.date >= date_from,
                ExpenseRow.date <= date_to,
            )
            .group_by(LineItemRow.category)
        )
        return {category: amount for category, amount in self._session.execute(query)}

    def confirmed_visits_by_category(self, date_from: dt.date, date_to: dt.date) -> dict[str, int]:
        """Per item category, the confirmed expenses dated `date_from`..`date_to`
        (inclusive) with at least one item in it (`COUNT(DISTINCT expense id)`), for the
        burn explanation (decision 0023). Every category is returned, as in
        `confirmed_spend_by_category`."""
        visits = func.count(func.distinct(ExpenseRow.id)).label("visits")
        query = (
            select(LineItemRow.category, visits)
            .join(ExpenseRow, LineItemRow.expense_id == ExpenseRow.id)
            .where(
                ExpenseRow.confirmed.is_(True),
                ExpenseRow.date >= date_from,
                ExpenseRow.date <= date_to,
            )
            .group_by(LineItemRow.category)
        )
        return {category: int(count) for category, count in self._session.execute(query)}

    def count(self) -> int:
        return self._session.scalar(select(func.count()).select_from(ExpenseRow)) or 0

    def list(self, filters: ExpenseFilter | None = None) -> list[ExpenseRecord]:
        """Newest date first, expenses without a date last, ties by id descending."""
        filters = filters or ExpenseFilter()
        query = _with_items().order_by(ExpenseRow.date.desc().nulls_last(), ExpenseRow.id.desc())
        if filters.review_status is not None:
            query = query.where(ExpenseRow.review_status == filters.review_status)
        if filters.confirmed is not None:
            query = query.where(ExpenseRow.confirmed == filters.confirmed)
        if filters.date_from is not None:
            query = query.where(ExpenseRow.date >= filters.date_from)
        if filters.date_to is not None:
            query = query.where(ExpenseRow.date <= filters.date_to)
        if filters.category is not None:
            with_category = select(LineItemRow.expense_id).where(
                LineItemRow.category == filters.category
            )
            query = query.where(ExpenseRow.id.in_(with_category))
        if filters.has_receipt is not None:
            query = query.where(
                ExpenseRow.receipt_id.is_not(None)
                if filters.has_receipt
                else ExpenseRow.receipt_id.is_(None)
            )
        return [to_record(row) for row in self._session.scalars(query)]

    def delete_unconfirmed_for_receipt(self, receipt_id: int) -> int:
        """Delete the receipt's expense and its items if it is unconfirmed; returns the count."""
        rows = self._session.scalars(
            select(ExpenseRow).where(
                ExpenseRow.receipt_id == receipt_id, ExpenseRow.confirmed.is_(False)
            )
        ).all()
        for row in rows:
            self._session.delete(row)
        self._session.flush()
        return len(rows)

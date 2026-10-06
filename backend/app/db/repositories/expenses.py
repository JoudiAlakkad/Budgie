"""Expense rows with their line items."""

import datetime as dt
from collections.abc import Iterable

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import ExpenseRow, LineItemRow
from app.db.records import (
    ExpenseFilter,
    ExpenseRecord,
    FlagRecord,
    LineItemRecord,
    NewExpense,
)


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
            flags=[
                {"field": flag.field, "code": flag.code, "message": flag.message}
                for flag in new.flags
            ],
            unreadable_fields=list(new.unreadable_fields),
            created_at=now,
            updated_at=now,
            line_items=[
                LineItemRow(
                    position=position,
                    description=item.description,
                    normalized_name=item.normalized_name,
                    qty=item.qty,
                    unit=item.unit,
                    unit_price=item.unit_price,
                    amount=item.amount,
                    category=item.category,
                    category_source=item.category_source,
                )
                for position, item in enumerate(new.line_items)
            ],
        )
        self._session.add(row)
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

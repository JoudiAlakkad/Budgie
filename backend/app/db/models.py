"""SQLAlchemy ORM models (docs/wiki/backend/persistence.md).

`receipts`, `expenses` and `line_items` are fixed in F05, `item_categories` in F07.
Money is stored as integer cents (`MoneyCents`), because SQLite has no decimal type and
`Numeric` goes through float; `qty` is stored as decimal text (`DecimalText`), because it
is unrounded. Timestamps are naive UTC.
"""

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Dialect,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

CENT = Decimal("0.01")


class Base(DeclarativeBase):
    """Declarative base for all tables."""


class MoneyCents(TypeDecorator[Decimal]):
    """A `Decimal` amount stored as integer cents; rounded half up to 0.01 on write."""

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> int | None:
        if value is None:
            return None
        cents = (Decimal(value) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        return int(cents)

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return (Decimal(int(value)) / 100).quantize(CENT)


class DecimalText(TypeDecorator[Decimal]):
    """An unrounded `Decimal` stored as its text, e.g. `1.234`."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return str(Decimal(value))

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return Decimal(value)


class ReceiptRow(Base):
    __tablename__ = "receipts"
    __table_args__ = {"sqlite_autoincrement": True}  # never reuse a deleted id

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The file name only (`<uuid4>.<ext>`), relative to UPLOAD_DIR.
    image_path: Mapped[str] = mapped_column(String(64))
    image_type: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), index=True)
    error: Mapped[str | None] = mapped_column(String(32))
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime)
    model_name: Mapped[str | None] = mapped_column(String(200))
    prompt_version: Mapped[str | None] = mapped_column(String(100))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    raw_model_output: Mapped[str | None] = mapped_column(Text)

    expense: Mapped["ExpenseRow | None"] = relationship(
        back_populates="receipt", cascade="all, delete-orphan", uselist=False
    )


class ExpenseRow(Base):
    __tablename__ = "expenses"
    __table_args__ = {"sqlite_autoincrement": True}  # never reuse a deleted id

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    receipt_id: Mapped[int | None] = mapped_column(
        ForeignKey("receipts.id", ondelete="CASCADE"), unique=True
    )
    merchant: Mapped[str | None] = mapped_column(Text)
    date: Mapped[dt.date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3))
    subtotal: Mapped[Decimal | None] = mapped_column(MoneyCents)
    tax: Mapped[Decimal | None] = mapped_column(MoneyCents)
    total: Mapped[Decimal | None] = mapped_column(MoneyCents)
    source: Mapped[str] = mapped_column(String(16))
    review_status: Mapped[str] = mapped_column(String(16))
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    flags: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    unreadable_fields: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime)

    receipt: Mapped[ReceiptRow | None] = relationship(back_populates="expense")
    line_items: Mapped[list["LineItemRow"]] = relationship(
        back_populates="expense",
        cascade="all, delete-orphan",
        order_by="LineItemRow.position",
    )


class LineItemRow(Base):
    __tablename__ = "line_items"
    __table_args__ = {"sqlite_autoincrement": True}  # never reuse a deleted id

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    expense_id: Mapped[int] = mapped_column(
        ForeignKey("expenses.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text)
    normalized_name: Mapped[str] = mapped_column(Text, index=True)
    qty: Mapped[Decimal | None] = mapped_column(DecimalText)
    unit: Mapped[str | None] = mapped_column(String(32))
    unit_price: Mapped[Decimal | None] = mapped_column(MoneyCents)
    amount: Mapped[Decimal] = mapped_column(MoneyCents)
    category: Mapped[str] = mapped_column(String(32), index=True)
    category_source: Mapped[str] = mapped_column(String(8))

    expense: Mapped[ExpenseRow] = relationship(back_populates="line_items")


class ItemCategoryRow(Base):
    """The item lookup table (decision 0013): normalised name -> category."""

    __tablename__ = "item_categories"

    normalized_name: Mapped[str] = mapped_column(Text, primary_key=True)
    category: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(8))  # seed | user
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime)

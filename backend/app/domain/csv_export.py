"""The CSV export's file format (contracts/csv-export.md; decision 0024).

Pure: rows in, text out. The service reads the confirmed expenses; this module sorts
them, formats every value and applies the formula guard.

- RFC 4180: comma, CRLF line ends, a header row; a field is quoted only if it contains a
  comma, a quote or a line break (stdlib `csv`, `QUOTE_MINIMAL`), a quote is doubled.
- Order: `date`, then `expense_id`, then the item's position on the receipt.
- Money has exactly 2 decimals with `.`; `item_qty` is a plain decimal without exponent
  or trailing zeros; `None` is an empty field.
- Formula guard: a value in one of `GUARDED_COLUMNS` that starts with one of
  `FORMULA_PREFIXES` gets a leading `'`. The guard is chosen by column, never by value,
  so a negative amount stays bare.
"""

import csv
import datetime as dt
import io
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from app.domain.money import cents

COLUMNS: tuple[str, ...] = (
    "expense_id",
    "date",
    "merchant",
    "currency",
    "expense_total",
    "item_description",
    "item_normalized_name",
    "item_qty",
    "item_unit",
    "item_amount",
    "category",
    "source",
)
"""The header row, in order; contracts/csv-export.md has the same table."""

GUARDED_COLUMNS = frozenset({"merchant", "item_description", "item_normalized_name", "item_unit"})
"""The free-text columns the formula guard applies to."""

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
"""First characters a spreadsheet may run as a formula (OWASP CSV injection)."""

FORMULA_ESCAPE = "'"

MEDIA_TYPE = "text/csv; charset=utf-8"


@dataclass(frozen=True)
class ExportRow:
    """One line item of a confirmed expense, with its expense's fields.

    `position` is the item's index on the receipt; it orders the rows but isn't a column.
    `date`, `merchant` and `expense_total` are set on every confirmed expense (confirm
    requires them); `None` would still be written as an empty field.
    """

    expense_id: int
    date: dt.date | None
    merchant: str | None
    currency: str
    expense_total: Decimal | None
    position: int
    item_description: str
    item_normalized_name: str
    item_qty: Decimal | None
    item_unit: str | None
    item_amount: Decimal
    category: str
    source: str


def format_money(value: Decimal | None) -> str:
    """Exactly 2 decimals, rounded half up: `-0.25`, `1.00`; empty for `None`."""
    if value is None:
        return ""
    rounded = cents(value)
    if rounded == 0:
        rounded = abs(rounded)  # no `-0.00`
    return f"{rounded:.2f}"


def format_qty(value: Decimal | None) -> str:
    """A plain decimal without exponent or trailing zeros: `1`, `1.234`, `100`."""
    if value is None:
        return ""
    if value == 0:
        return "0"
    return f"{value.normalize():f}"


def guard(value: str | None) -> str:
    """The formula guard: a leading `'` if the text starts like a formula."""
    if value is None:
        return ""
    return FORMULA_ESCAPE + value if value.startswith(FORMULA_PREFIXES) else value


def row_values(row: ExportRow) -> tuple[str, ...]:
    """The row's fields in `COLUMNS` order, formatted and guarded."""
    raw: dict[str, str] = {
        "expense_id": str(row.expense_id),
        "date": row.date.isoformat() if row.date is not None else "",
        "merchant": row.merchant or "",
        "currency": row.currency,
        "expense_total": format_money(row.expense_total),
        "item_description": row.item_description,
        "item_normalized_name": row.item_normalized_name,
        "item_qty": format_qty(row.item_qty),
        "item_unit": row.item_unit or "",
        "item_amount": format_money(row.item_amount),
        "category": row.category,
        "source": row.source,
    }
    return tuple(guard(raw[name]) if name in GUARDED_COLUMNS else raw[name] for name in COLUMNS)


def _order(row: ExportRow) -> tuple[bool, dt.date, int, int]:
    # A missing date (not possible for a confirmed expense) sorts last.
    return (row.date is None, row.date or dt.date.min, row.expense_id, row.position)


def render(rows: Iterable[ExportRow]) -> str:
    """The whole file: header row, then the rows sorted by date, expense id, position."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(COLUMNS)
    writer.writerows(row_values(row) for row in sorted(rows, key=_order))
    return buffer.getvalue()


def export_filename(date_from: dt.date | None, date_to: dt.date | None) -> str:
    """`budgie-expenses_<from|all>_<to|all>.csv`, from ISO dates only (no header injection)."""
    start = date_from.isoformat() if date_from is not None else "all"
    end = date_to.isoformat() if date_to is not None else "all"
    return f"budgie-expenses_{start}_{end}.csv"

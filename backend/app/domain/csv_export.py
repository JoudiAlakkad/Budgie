"""The CSV export's file format (contracts/csv-export.md; decision 0024).

Pure: rows in, UTF-8 bytes out. The service reads the confirmed expenses; this module
sorts them (the export's only sort), formats every value and applies the formula guard.

- RFC 4180: comma, CRLF line ends, a header row; a field is quoted only if it contains
  one of `QUOTE_TRIGGERS` (comma, quote, line break or `;`), a quote inside is doubled.
  Stdlib `csv` can't quote on `;` with a comma delimiter, so `quote_field` does it.
- Order: `date`, then `expense_id`, then the item's position on the receipt.
- Money has exactly 2 decimals with `.`; `None` is an empty field (a confirmed expense
  has none).
- Formula guard: a value in one of `GUARDED_COLUMNS` gets a leading `'` when it starts
  with one of `CONTROL_PREFIXES` or `'`, or when its first character after leading
  whitespace is one of `FORMULA_SIGNS`. The guard is chosen by column, never by value,
  so a negative amount stays bare.
"""

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
    "item_amount",
    "category",
    "source",
)
"""The header row, in order; contracts/csv-export.md has the same table."""

GUARDED_COLUMNS = frozenset({"merchant", "item_description", "item_normalized_name"})
"""The free-text columns the formula guard applies to."""

FORMULA_SIGNS = ("=", "+", "-", "@", "＝", "＋", "－", "＠")
"""Characters that start a formula (OWASP CSV injection), with their full-width forms
`＝`, `＋`, `－`, `＠`; checked after leading whitespace, which some spreadsheets trim."""

CONTROL_PREFIXES = ("\t", "\r")
"""First characters that are guarded on their own."""

FORMULA_ESCAPE = "'"
"""Also prefixed when it is already the first character, so stripping exactly one leading
`'` from a guarded column always gives back the stored value."""

QUOTE_TRIGGERS = frozenset({",", '"', "\r", "\n", ";"})
"""A field containing any of these is quoted; `;` so that a spreadsheet splitting on `;`
keeps it one cell (decision 0024)."""

DELIMITER = ","
LINE_END = "\r\n"
ENCODING = "utf-8"
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


def guard(value: str | None) -> str:
    """The formula guard: a leading `'` in front of the whole value if it starts with a
    tab, a CR or `'`, or if it starts like a formula after leading whitespace."""
    if value is None:
        return ""
    if value.startswith((*CONTROL_PREFIXES, FORMULA_ESCAPE)) or value.lstrip().startswith(
        FORMULA_SIGNS
    ):
        return FORMULA_ESCAPE + value
    return value


def quote_field(value: str) -> str:
    """RFC 4180: in quotes with inner quotes doubled if it contains one of
    `QUOTE_TRIGGERS`, otherwise as is."""
    if QUOTE_TRIGGERS.isdisjoint(value):
        return value
    return '"' + value.replace('"', '""') + '"'


def format_line(values: Iterable[str]) -> str:
    """One record, fields quoted where needed, with its CRLF line end."""
    return DELIMITER.join(quote_field(value) for value in values) + LINE_END


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
        "item_amount": format_money(row.item_amount),
        "category": row.category,
        "source": row.source,
    }
    return tuple(guard(raw[name]) if name in GUARDED_COLUMNS else raw[name] for name in COLUMNS)


def _order(row: ExportRow) -> tuple[bool, dt.date, int, int]:
    # A missing date (not possible for a confirmed expense) sorts last.
    return (row.date is None, row.date or dt.date.min, row.expense_id, row.position)


def render(rows: Iterable[ExportRow]) -> bytes:
    """The whole file in UTF-8 without BOM: header row, then the rows sorted by date,
    expense id, position (the export's only sort). Encoded while it is written, so the
    file is held in memory once."""
    buffer = io.BytesIO()
    with io.TextIOWrapper(buffer, encoding=ENCODING, newline="", write_through=True) as text:
        text.write(format_line(COLUMNS))
        for row in sorted(rows, key=_order):
            text.write(format_line(row_values(row)))
        text.flush()
        return buffer.getvalue()


def export_filename(date_from: dt.date | None, date_to: dt.date | None) -> str:
    """`budgie-expenses_<from|all>_<to|all>.csv`, from ISO dates only (no header injection)."""
    start = date_from.isoformat() if date_from is not None else "all"
    end = date_to.isoformat() if date_to is not None else "all"
    return f"budgie-expenses_{start}_{end}.csv"

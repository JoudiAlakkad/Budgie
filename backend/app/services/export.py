"""`GET /expenses/export.csv`: confirmed expenses as CSV, one row per line item
(contracts/csv-export.md; decision 0024).

A service of its own, not a method on `ExpenseService`: that class has a method named
`list`, which breaks `-> list[...]` annotations on Python 3.12 (workflow.md#ci-checks).
The file is built in memory before the response starts, so a storage error is a JSON
`500`, never a truncated file. Rows are never logged.
"""

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass

from app.db.records import ExpenseFilter, ExpenseRecord
from app.db.repositories.expenses import ExpenseRepository
from app.db.session import Database
from app.domain import csv_export
from app.domain.csv_export import ExportRow

CSV_COLUMNS = csv_export.COLUMNS
"""The header row, for the route's OpenAPI description."""

CSV_MEDIA_TYPE = csv_export.MEDIA_TYPE


@dataclass(frozen=True)
class CsvFile:
    """The rendered file, already UTF-8 encoded, and the name the download gets."""

    filename: str
    data: bytes


def export_rows(expenses: Iterable[ExpenseRecord]) -> tuple[ExportRow, ...]:
    """One row per line item; deposit and discount items included."""
    return tuple(
        ExportRow(
            expense_id=expense.id,
            date=expense.date,
            merchant=expense.merchant,
            currency=expense.currency,
            expense_total=expense.total,
            position=item.position,
            item_description=item.description,
            item_normalized_name=item.normalized_name,
            item_qty=item.qty,
            item_unit=item.unit,
            item_amount=item.amount,
            category=item.category,
            source=expense.source,
        )
        for expense in expenses
        for item in expense.line_items
    )


class ExportService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def expenses_csv(self, date_from: dt.date | None, date_to: dt.date | None) -> CsvFile:
        """The confirmed expenses dated `date_from`..`date_to` (both inclusive and
        optional) as CSV. `date_from` after `date_to`, or no match, gives the header only."""
        filters = ExpenseFilter(confirmed=True, date_from=date_from, date_to=date_to)
        with self._db.transaction() as session:
            expenses = ExpenseRepository(session).list(filters)
        # The repository's order doesn't matter: `csv_export.render` does the only sort.
        return CsvFile(
            filename=csv_export.export_filename(date_from, date_to),
            data=csv_export.render(export_rows(expenses)),
        )

"""`get_today`: the Europe/Berlin date, read when the date rules run."""

import datetime as dt
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db.images import ImageStore
from app.db.session import Database
from app.services.categorization import PlaceholderCategorizer
from app.services.dependencies import berlin_date, get_today, today_in_berlin
from app.services.expenses import ExpenseService

UTC = dt.UTC


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        (dt.datetime(2026, 10, 5, 21, 59, tzinfo=UTC), dt.date(2026, 10, 5)),  # 23:59 CEST
        (dt.datetime(2026, 10, 5, 22, 0, tzinfo=UTC), dt.date(2026, 10, 6)),  # 00:00 CEST
        (dt.datetime(2026, 12, 31, 22, 59, tzinfo=UTC), dt.date(2026, 12, 31)),  # 23:59 CET
        (dt.datetime(2026, 12, 31, 23, 0, tzinfo=UTC), dt.date(2027, 1, 1)),  # 00:00 CET
    ],
)
def test_berlin_date(instant: dt.datetime, expected: dt.date) -> None:
    assert berlin_date(instant) == expected


def test_the_provider_is_a_clock_not_a_date() -> None:
    clock = get_today()

    assert clock is today_in_berlin
    assert isinstance(clock(), dt.date)


def test_manual_create_reads_the_clock_when_it_assesses(tmp_path: Path) -> None:
    db = Database(f"sqlite:///{tmp_path / 'budgie.db'}")
    db.init_db()
    reads: list[dt.date] = []

    def clock() -> dt.date:
        reads.append(dt.date(2026, 10, 5))
        return reads[-1]

    service = ExpenseService(db, PlaceholderCategorizer(), clock, ImageStore(str(tmp_path)))
    assert reads == []
    item = SimpleNamespace(
        description="BROT", qty=None, unit=None, unit_price=None, amount=Decimal("1"), category=None
    )
    body = SimpleNamespace(
        receipt_id=None,
        merchant="REWE",
        date=dt.date(2026, 10, 6),  # tomorrow by the clock
        currency="EUR",
        total=Decimal("1"),
        subtotal=None,
        tax=None,
        line_items=[item],
    )

    expense = service.create(body)  # type: ignore[arg-type]

    assert len(reads) == 1
    assert "date_in_future" in [flag.code for flag in expense.flags]
    db.dispose()

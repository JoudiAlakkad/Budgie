"""`budgets`, `savings_goal`, the spend query and the counts (F08; persistence.md)."""

import datetime as dt
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.records import BudgetRecord, FlagRecord, GoalRecord, NewExpense, NewLineItem
from app.db.repositories.budgets import BudgetRepository, GoalRepository
from app.db.repositories.expenses import ExpenseRepository
from app.db.repositories.receipts import ReceiptRepository
from app.db.session import Database
from app.errors import InvalidFields, StorageError
from app.services.budgets import BudgetService
from app.services.demo_seed import SeedBudget

D = Decimal


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database(f"sqlite:///{tmp_path / 'budgie.db'}")
    database.init_db()
    yield database
    database.dispose()


def item(category: str, amount: str) -> NewLineItem:
    return NewLineItem(
        description=category,
        normalized_name=category,
        qty=None,
        unit=None,
        unit_price=None,
        amount=D(amount),
        category=category,
        category_source="seed",
    )


def add_expense(db: Database, day: dt.date, *items: NewLineItem, confirmed: bool = True) -> int:
    new = NewExpense(
        receipt_id=None,
        merchant="REWE",
        date=day,
        currency="EUR",
        subtotal=None,
        tax=None,
        total=sum((i.amount for i in items), D(0)),
        source="manual",
        review_status="accepted",
        flags=(),
        unreadable_fields=(),
        line_items=items,
        confirmed=confirmed,
    )
    with db.transaction() as session:
        return ExpenseRepository(session).insert(new).id


# ---------------------------------------------------------------- budgets


def test_replace_swaps_the_whole_list(db: Database) -> None:
    with db.transaction() as session:
        first = BudgetRepository(session).replace(
            [BudgetRecord("snacks_sweets", D("40")), BudgetRecord("drinks", D("20.005"))]
        )
    with db.transaction() as session:
        second = BudgetRepository(session).replace([BudgetRecord("eating_out", D("60"))])
    with db.transaction() as session:
        stored = BudgetRepository(session).all()

    # by category; money rounded half up to cents as stored
    assert first == (BudgetRecord("drinks", D("20.01")), BudgetRecord("snacks_sweets", D("40.00")))
    assert second == stored == (BudgetRecord("eating_out", D("60.00")),)


def test_replace_with_nothing_clears_the_list(db: Database) -> None:
    with db.transaction() as session:
        BudgetRepository(session).replace([BudgetRecord("drinks", D("20"))])
    with db.transaction() as session:
        assert BudgetRepository(session).replace([]) == ()
        assert BudgetRepository(session).count() == 0


def test_a_failed_replace_keeps_the_old_list(db: Database) -> None:
    with db.transaction() as session:
        BudgetRepository(session).replace([BudgetRecord("drinks", D("20"))])

    with pytest.raises(StorageError), db.transaction() as session:
        # a repeated category violates the primary key after the delete
        BudgetRepository(session).replace(
            [BudgetRecord("other", D("1")), BudgetRecord("other", D("2"))]
        )

    with db.transaction() as session:
        assert BudgetRepository(session).all() == (BudgetRecord("drinks", D("20.00")),)


# ---------------------------------------------------------------- goal


def test_goal_is_none_until_put(db: Database) -> None:
    with db.transaction() as session:
        assert GoalRepository(session).get() is None


def test_put_goal_upserts_the_single_row(db: Database) -> None:
    first = GoalRecord(D("1200"), dt.date(2027, 3, 31), D("1400"))
    second = GoalRecord(D("500.555"), dt.date(2027, 6, 30), None)
    with db.transaction() as session:
        assert GoalRepository(session).put(first) == GoalRecord(
            D("1200.00"), dt.date(2027, 3, 31), D("1400.00")
        )
    with db.transaction() as session:
        stored = GoalRepository(session).put(second)
    with db.transaction() as session:
        read = GoalRepository(session).get()

    assert stored == read == GoalRecord(D("500.56"), dt.date(2027, 6, 30), None)
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT id FROM savings_goal")).scalars().all() == [1]


def test_the_check_constraint_allows_only_id_1(db: Database) -> None:
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO savings_goal (id, target_amount, target_date) "
                "VALUES (2, 100, '2027-01-01')"
            )
        )


# ---------------------------------------------------------------- spend and counts


def test_spend_by_category_sums_confirmed_items_in_the_range(db: Database) -> None:
    add_expense(db, dt.date(2026, 10, 1), item("drinks", "1.10"), item("deposit", "0.25"))
    add_expense(db, dt.date(2026, 10, 31), item("drinks", "2.20"), item("eating_out", "5.00"))
    add_expense(db, dt.date(2026, 10, 15), item("drinks", "9.99"), confirmed=False)  # draft
    add_expense(db, dt.date(2026, 9, 30), item("drinks", "50.00"))  # before
    add_expense(db, dt.date(2026, 11, 1), item("drinks", "50.00"))  # after
    add_expense(db, dt.date(2026, 10, 2), item("discount", "-0.50"), item("uncategorized", "3"))

    with db.transaction() as session:
        spend = ExpenseRepository(session).confirmed_spend_by_category(
            dt.date(2026, 10, 1), dt.date(2026, 10, 31)
        )

    assert spend == {
        "drinks": D("3.30"),
        "deposit": D("0.25"),
        "eating_out": D("5.00"),
        "discount": D("-0.50"),
        "uncategorized": D("3.00"),
    }
    assert all(type(amount) is Decimal for amount in spend.values())


def test_spend_without_expenses_is_empty(db: Database) -> None:
    with db.transaction() as session:
        assert (
            ExpenseRepository(session).confirmed_spend_by_category(
                dt.date(2026, 10, 1), dt.date(2026, 10, 31)
            )
            == {}
        )


def test_spend_skips_expenses_without_a_date(db: Database) -> None:
    new = NewExpense(
        receipt_id=None,
        merchant=None,
        date=None,
        currency="EUR",
        subtotal=None,
        tax=None,
        total=None,
        source="ai",
        review_status="needs_review",
        flags=(FlagRecord("date", "missing_date", "no date"),),
        unreadable_fields=(),
        line_items=(item("drinks", "1.00"),),
        confirmed=True,
    )
    with db.transaction() as session:
        ExpenseRepository(session).insert(new)
        assert (
            ExpenseRepository(session).confirmed_spend_by_category(
                dt.date(1, 1, 1), dt.date(9999, 12, 31)
            )
            == {}
        )


def test_counts(db: Database) -> None:
    with db.transaction() as session:
        assert ExpenseRepository(session).count() == 0
        assert ReceiptRepository(session).count() == 0
        ReceiptRepository(session).create("a.jpg", "jpeg", dt.datetime(2026, 10, 1))
    add_expense(db, dt.date(2026, 10, 1), item("drinks", "1"))
    add_expense(db, dt.date(2026, 10, 2), item("drinks", "1"), confirmed=False)

    with db.transaction() as session:
        assert ExpenseRepository(session).count() == 2
        assert ReceiptRepository(session).count() == 1


def test_the_service_rejects_a_non_positive_limit_without_the_schema(db: Database) -> None:
    """The API schema already rejects it; the service guards callers like the demo seed."""
    service = BudgetService(db, lambda: dt.date(2026, 10, 15))
    with pytest.raises(InvalidFields) as error:
        service.replace_budgets([SeedBudget("drinks", D("5")), SeedBudget("other", D("0"))])

    assert error.value.fields == (("1.monthly_limit", "The limit must be greater than 0."),)
    assert service.budgets() == []

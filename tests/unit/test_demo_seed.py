"""The demo seed: `services/demo_seed.py` and the `python -m app.demo_seed` CLI (decision 0022)."""

import datetime as dt
from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path

import pytest

from app import demo_seed as cli
from app.config import Settings
from app.db.images import ImageStore
from app.db.records import BudgetRecord, GoalRecord, NewExpense
from app.db.repositories.budgets import BudgetRepository, GoalRepository
from app.db.repositories.expenses import ExpenseRepository
from app.db.repositories.item_categories import ItemCategoryRepository
from app.db.repositories.receipts import ReceiptRepository
from app.db.session import Database
from app.domain.budget import Month
from app.domain.categorize import normalize
from app.errors import InvalidState, StorageError
from app.services.demo_seed import demo_plan, seed_demo
from app.services.dependencies import database_for, dispose_databases
from app.services.insights import InsightsService
from app.services.item_categories import load_seed
from app.services.storage import prepare_storage

TODAYS = [
    dt.date(2026, 10, 15),
    dt.date(2026, 10, 1),
    dt.date(2024, 2, 29),
    dt.date(2026, 12, 31),
    dt.date(2027, 1, 3),
]
SCENARIO = dt.date(2026, 10, 15)


@pytest.fixture
def db(settings: Settings) -> Iterator[Database]:
    """A prepared database: tables plus the item seed, as at startup."""
    assert prepare_storage(settings).ok
    yield database_for(settings.database_url)
    dispose_databases()


@pytest.fixture
def images(settings: Settings) -> ImageStore:
    return ImageStore(settings.upload_dir)


def counts(db: Database) -> tuple[int, int, int, bool]:
    with db.transaction() as session:
        return (
            ExpenseRepository(session).count(),
            ReceiptRepository(session).count(),
            BudgetRepository(session).count(),
            GoalRepository(session).get() is not None,
        )


# ---------------------------------------------------------------- the plan


@pytest.mark.parametrize("today", TODAYS, ids=str)
def test_every_description_is_a_seed_name_and_items_sum_to_the_total(today: dt.date) -> None:
    seed = load_seed()

    for expense in demo_plan(today).expenses:
        assert expense.total == sum(item.amount for item in expense.line_items)
        for item in expense.line_items:
            assert normalize(item.description).name in seed, item.description
            assert item.category is None  # the seed decides; nothing is a user choice


@pytest.mark.parametrize("today", TODAYS, ids=str)
def test_the_plan_covers_two_full_months_and_the_current_one_up_to_today(
    today: dt.date,
) -> None:
    plan = demo_plan(today)
    current = Month.of(today)
    dates = [expense.date for expense in plan.expenses]

    assert min(dates) >= current.shifted(-2).first
    assert max(dates) <= today
    assert all(day.day <= 28 for day in dates)
    assert {Month.of(day) for day in dates} == {current.shifted(-2), current.shifted(-1), current}
    keys = [(e.merchant, e.date, e.total) for e in plan.expenses]
    assert len(keys) == len(set(keys))  # nothing for the duplicate rule to find
    assert plan.goal.target_date == current.shifted(5).last
    assert plan.goal.target_amount == Decimal("1200.00")
    assert plan.goal.monthly_income == Decimal("1400.00")


def test_the_plan_is_deterministic() -> None:
    assert demo_plan(SCENARIO) == demo_plan(SCENARIO)
    assert demo_plan(SCENARIO) != demo_plan(dt.date(2026, 10, 16))


def test_on_the_first_only_day_one_of_the_current_month_is_planned() -> None:
    current = [e for e in demo_plan(dt.date(2026, 10, 1)).expenses if e.date.month == 10]

    assert [e.date for e in current] == [dt.date(2026, 10, 1)]


# ---------------------------------------------------------------- seeding


@pytest.mark.parametrize("today", TODAYS, ids=str)
def test_seeded_expenses_are_confirmed_accepted_and_seed_categorised(
    db: Database, images: ImageStore, today: dt.date
) -> None:
    with db.transaction() as session:
        before = ItemCategoryRepository(session).table()

    result = seed_demo(db, images, today)

    with db.transaction() as session:
        expenses = ExpenseRepository(session).list()
        after = ItemCategoryRepository(session).table()
    plan = demo_plan(today)
    assert result.expenses == len(expenses) == len(plan.expenses)
    for expense in expenses:
        assert expense.confirmed
        assert (expense.review_status, expense.flags) == ("accepted", ())
        assert expense.source == "manual"
        assert all(item.category_source == "seed" for item in expense.line_items)
        assert Month.of(today).shifted(-2).first <= expense.date <= today  # type: ignore[operator]
    assert after == before  # no user rows; the table is untouched
    assert all(source == "seed" for _, source in after.values())
    assert counts(db) == (len(plan.expenses), 0, len(plan.budgets), True)
    assert (result.first_day, result.last_day) == (
        min(e.date for e in plan.expenses),
        max(e.date for e in plan.expenses),
    )


def test_seeding_is_deterministic(settings: Settings, tmp_path: Path, images: ImageStore) -> None:
    def seeded(url: str) -> list[tuple[object, ...]]:
        prepare_storage(settings.model_copy(update={"database_url": url}))
        db = database_for(url)
        seed_demo(db, images, SCENARIO)
        with db.transaction() as session:
            return [
                (
                    e.id,
                    e.merchant,
                    e.date,
                    e.total,
                    tuple((i.description, i.amount, i.category) for i in e.line_items),
                )
                for e in ExpenseRepository(session).list()
            ]

    try:
        first = seeded(f"sqlite:///{tmp_path / 'one.db'}")
        second = seeded(f"sqlite:///{tmp_path / 'two.db'}")
    finally:
        dispose_databases()

    assert first == second


def test_scenario_two_at_mid_october(db: Database, images: ImageStore) -> None:
    seed_demo(db, images, SCENARIO)

    summary = InsightsService(db, lambda: SCENARIO).summary()

    states = {row.category: row.state for row in summary.categories}
    assert summary.month == "2026-10"
    assert states["electronics"] == "over"
    assert states["eating_out"] == "on_pace_to_overrun"
    assert states["groceries.fresh"] == "under"
    assert summary.total_budget == Decimal("370.00")
    assert summary.goal is not None
    assert summary.goal.required_per_month == Decimal("200.00")  # 1200 over Oct..Mar
    assert summary.goal.saved_this_month == Decimal("1400.00") - summary.projected_total
    # A past month: the projection equals the spend.
    september = InsightsService(db, lambda: SCENARIO).summary("2026-09")
    assert september.projected_total == september.total_spent
    assert all(row.state == "under" for row in september.categories)


def _add_expense(db: Database) -> None:
    new = NewExpense(
        receipt_id=None,
        merchant="REWE",
        date=dt.date(2026, 10, 1),
        currency="EUR",
        subtotal=None,
        tax=None,
        total=Decimal("1"),
        source="manual",
        review_status="accepted",
        flags=(),
        unreadable_fields=(),
        line_items=(),
    )
    with db.transaction() as session:
        ExpenseRepository(session).insert(new)


def _add_receipt(db: Database) -> None:
    with db.transaction() as session:
        ReceiptRepository(session).create("a.jpg", "jpeg", dt.datetime(2026, 10, 1))


def _add_budget(db: Database) -> None:
    with db.transaction() as session:
        BudgetRepository(session).replace([BudgetRecord("drinks", Decimal("20"))])


def _add_goal(db: Database) -> None:
    with db.transaction() as session:
        GoalRepository(session).put(GoalRecord(Decimal("100"), dt.date(2027, 1, 1), None))


@pytest.mark.parametrize(
    "fill",
    [_add_expense, _add_receipt, _add_budget, _add_goal],
    ids=["expense", "receipt", "budget", "goal"],
)
def test_a_non_empty_database_is_refused_unchanged(
    db: Database, images: ImageStore, fill: Callable[[Database], None]
) -> None:
    fill(db)
    before = counts(db)

    with pytest.raises(InvalidState):
        seed_demo(db, images, SCENARIO)

    assert counts(db) == before


def test_a_second_seed_is_refused(db: Database, images: ImageStore) -> None:
    seed_demo(db, images, SCENARIO)
    before = counts(db)

    with pytest.raises(InvalidState):
        seed_demo(db, images, SCENARIO)

    assert counts(db) == before


# ---------------------------------------------------------------- CLI


def test_cli_loads_then_refuses(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--today", "2026-10-15"], settings) == cli.EXIT_OK
    loaded = capsys.readouterr().out
    assert cli.main(["--today", "2026-10-15"], settings) == cli.EXIT_NOT_EMPTY
    refused = capsys.readouterr().err

    assert "Loaded 46 confirmed expenses" in loaded
    assert "empty database" in refused
    db = database_for(settings.database_url)
    try:
        assert counts(db) == (46, 0, 8, True)
    finally:
        dispose_databases()


def test_cli_reports_unusable_storage(settings: Settings, tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    broken = settings.model_copy(update={"upload_dir": str(blocker / "uploads")})

    assert cli.main(["--today", "2026-10-15"], broken) == cli.EXIT_STORAGE


def test_cli_rejects_a_bad_date(settings: Settings) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--today", "2026-13-01"], settings)

    assert exit_info.value.code == 2


def test_cli_maps_a_storage_error_during_the_seed(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object) -> None:
        raise StorageError()

    monkeypatch.setattr(cli, "seed_demo", broken)

    assert cli.main(["--today", "2026-10-15"], settings) == cli.EXIT_STORAGE

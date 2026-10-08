"""Demo data for scenarios 2 and 3, loaded through the services (decision 0022).

The plan is deterministic for a given `today`: the two previous months are full and the
current month holds the days up to `today`. Days are 1 to 28 only, so every month has
them. Every description normalises to a name in the item seed and the items sum to the
total, so each expense is accepted without flags and confirmable; no item sends a
category, so nothing is written to the lookup table as a user choice.

Scenario 2 at mid-month: `electronics` is `over` (a day-1 purchase above its budget, in
every current month), and `eating_out` is `on_pace_to_overrun` around the 15th.
"""

import datetime as dt
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.db.images import ImageStore
from app.db.repositories.budgets import BudgetRepository, GoalRepository
from app.db.repositories.expenses import ExpenseRepository
from app.db.repositories.receipts import ReceiptRepository
from app.db.session import Database
from app.domain.budget import Month
from app.errors import InvalidState
from app.services.budgets import BudgetService
from app.services.categorization import LookupCategorizer
from app.services.expenses import ExpenseService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SeedItem:
    """Satisfies `services.expenses.LineItemInput`; no category, so the seed decides."""

    description: str
    amount: Decimal
    qty: Decimal | None = None
    unit: str | None = None
    unit_price: Decimal | None = None
    category: str | None = None


@dataclass(frozen=True)
class SeedExpense:
    """Satisfies `services.expenses.ExpenseInput`; `total` is the sum of the items."""

    merchant: str
    date: dt.date
    line_items: tuple[SeedItem, ...]
    receipt_id: int | None = None
    currency: str = "EUR"
    subtotal: Decimal | None = None
    tax: Decimal | None = None

    @property
    def total(self) -> Decimal:
        return sum((item.amount for item in self.line_items), Decimal("0.00"))


@dataclass(frozen=True)
class SeedBudget:
    category: str
    monthly_limit: Decimal


@dataclass(frozen=True)
class SeedGoal:
    target_amount: Decimal
    target_date: dt.date
    monthly_income: Decimal | None


@dataclass(frozen=True)
class DemoPlan:
    expenses: tuple[SeedExpense, ...]
    budgets: tuple[SeedBudget, ...]
    goal: SeedGoal


@dataclass(frozen=True)
class DemoSeedResult:
    expenses: int
    budgets: int
    goal: bool
    first_day: dt.date
    last_day: dt.date


def _items(*pairs: tuple[str, str]) -> tuple[SeedItem, ...]:
    return tuple(SeedItem(description, Decimal(amount)) for description, amount in pairs)


# Day of month -> (merchant, items), repeated every month. Days are unique, so no two
# expenses share merchant, date and total (the duplicate rule).
MONTHLY: dict[int, tuple[str, tuple[SeedItem, ...]]] = {
    2: (
        "REWE",
        _items(
            ("BIO BANANE 1 KG", "1.89"),
            ("Milch", "1.19"),
            ("Vollkornbrot", "2.49"),
            ("Tomaten", "2.29"),
            ("Gouda", "2.79"),
            ("Nudeln", "1.29"),
            ("Mineralwasser", "3.54"),
            ("Pfand", "1.50"),
        ),
    ),
    3: ("Café Central", _items(("Cappuccino", "3.80"), ("Muffin", "2.90"))),
    4: (
        "Deutsche Bahn",
        _items(
            ("Tageskarte", "9.90"),
        ),
    ),
    5: (
        "dm",
        _items(
            ("Zahnpasta", "1.95"),
            ("Duschgel", "1.45"),
            ("Shampoo", "2.95"),
            ("Toilettenpapier", "3.95"),
        ),
    ),
    6: ("Kebab Haus", _items(("Döner Kebab", "7.50"), ("Cola", "2.50"))),
    8: (
        "Kiosk am Markt",
        _items(("Schokolade", "1.49"), ("Chips", "1.99"), ("Gummibärchen", "1.19")),
    ),
    9: (
        "ALDI",
        _items(
            ("Äpfel", "2.49"),
            ("Joghurt", "0.89"),
            ("Eier", "2.29"),
            ("Kartoffeln", "2.99"),
            ("Butter", "2.29"),
            ("Reis", "1.79"),
            ("Kaffee", "4.99"),
        ),
    ),
    10: (
        "Café Central",
        _items(
            ("Latte Macchiato", "4.20"),
        ),
    ),
    13: ("Burgerei", _items(("Burger", "11.90"), ("Pommes", "3.90"))),
    14: ("Getränkemarkt", _items(("Bier", "4.74"), ("Pfand", "0.48"))),
    16: (
        "REWE",
        _items(
            ("Banane", "1.79"),
            ("Milch", "1.19"),
            ("Brot", "2.29"),
            ("Paprika", "1.99"),
            ("Hähnchenbrust", "5.49"),
            ("Spaghetti", "1.19"),
            ("Pfandrückgabe", "-0.75"),
        ),
    ),
    18: (
        "Deutsche Bahn",
        _items(
            ("Fahrkarte", "29.90"),
        ),
    ),
    19: ("dm", _items(("Deo", "2.45"), ("Küchenrolle", "2.75"))),
    20: (
        "Café Central",
        _items(
            ("Cappuccino", "3.80"),
        ),
    ),
    22: ("Kiosk am Markt", _items(("Kekse", "1.79"), ("Red Bull", "1.49"))),
    23: (
        "Lidl",
        _items(
            ("Salat", "1.29"),
            ("Käse", "2.49"),
            ("Toastbrot", "1.39"),
            ("Haferflocken", "0.99"),
            ("Olivenöl", "5.99"),
            ("Zwiebeln", "1.29"),
        ),
    ),
    26: ("Kebab Haus", _items(("Currywurst", "4.50"), ("Pommes", "3.50"))),
}

# One-offs by month offset from the current month (0 = current, -1 = previous).
ONE_OFFS: dict[int, dict[int, tuple[str, tuple[SeedItem, ...]]]] = {
    0: {
        1: (
            "MediaMarkt",
            _items(
                ("Kopfhörer", "59.99"),
            ),
        )
    },
    -1: {11: ("H&M", _items(("Socken", "7.99"), ("Shirt", "12.99")))},
}

BUDGETS: tuple[SeedBudget, ...] = tuple(
    SeedBudget(category, Decimal(limit))
    for category, limit in (
        ("groceries.fresh", "120.00"),
        ("groceries.staples", "50.00"),
        ("eating_out", "60.00"),
        ("snacks_sweets", "20.00"),
        ("household", "25.00"),
        ("personal_care", "20.00"),
        ("transport", "50.00"),
        ("electronics", "25.00"),
    )
)

GOAL_TARGET = Decimal("1200.00")
GOAL_MONTHS_AHEAD = 5
MONTHLY_INCOME = Decimal("1400.00")


def demo_plan(today: dt.date) -> DemoPlan:
    """Months M-2 and M-1 in full, month M up to `today`; budgets and a goal of 1200 by the
    end of month M+5 with an income of 1400."""
    current = Month.of(today)
    expenses: list[SeedExpense] = []
    for offset in (-2, -1, 0):
        month = current.shifted(offset)
        days = MONTHLY | ONE_OFFS.get(offset, {})
        for day in sorted(days):
            date = dt.date(month.year, month.month, day)
            if date > today:
                continue
            merchant, items = days[day]
            expenses.append(SeedExpense(merchant=merchant, date=date, line_items=items))
    goal = SeedGoal(
        target_amount=GOAL_TARGET,
        target_date=current.shifted(GOAL_MONTHS_AHEAD).last,
        monthly_income=MONTHLY_INCOME,
    )
    return DemoPlan(expenses=tuple(expenses), budgets=BUDGETS, goal=goal)


def is_empty(db: Database) -> bool:
    """No expense, receipt, budget or goal; `item_categories` doesn't count (decision 0022)."""
    with db.transaction() as session:
        return (
            ExpenseRepository(session).count() == 0
            and ReceiptRepository(session).count() == 0
            and BudgetRepository(session).count() == 0
            and GoalRepository(session).get() is None
        )


def seed_demo(db: Database, images: ImageStore, today: dt.date) -> DemoSeedResult:
    """Load the demo plan for `today` into an empty database.

    `InvalidState`, changing nothing, if the database holds any expense, receipt, budget
    or goal. Each expense is created and confirmed through `ExpenseService` (one
    transaction each, so a failure halfway leaves a partial seed), then the budgets and
    the goal are set through `BudgetService`.
    """
    if not is_empty(db):
        raise InvalidState(
            "The database already holds expenses, receipts, budgets or a goal; "
            "the demo data loads only into an empty database."
        )
    plan = demo_plan(today)

    def clock() -> dt.date:
        return today

    expenses = ExpenseService(db, LookupCategorizer(), clock, images)
    for planned in plan.expenses:
        created = expenses.create(planned)
        expenses.confirm(created.id)
    settings = BudgetService(db, clock)
    settings.replace_budgets(plan.budgets)
    settings.put_goal(plan.goal)
    days: Sequence[dt.date] = [expense.date for expense in plan.expenses]
    result = DemoSeedResult(
        expenses=len(plan.expenses),
        budgets=len(plan.budgets),
        goal=True,
        first_day=min(days),
        last_day=max(days),
    )
    logger.info(
        "Demo data loaded: %d expenses, %d budgets and a goal", result.expenses, result.budgets
    )
    return result

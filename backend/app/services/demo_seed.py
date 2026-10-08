"""Demo data for scenarios 2 and 3, loaded through the services (decision 0022).

The plan is deterministic for a given `today`: the two previous months are full and the
current month holds the days up to `today`. Days are 1 to 28 only, so every month has
them. Every description normalises to a name in the item seed and the items sum to the
total, so each expense is accepted without flags and confirmable; no item sends a
category, so nothing is written to the lookup table as a user choice.

Scenario 2 on every day of the current month but its last: `electronics` is `over` (a
day-1 purchase above its budget) and `health` is `on_pace_to_overrun` (a day-1 purchase
just under its budget, extrapolated over the month). On the last day the projection
equals the spend, so nothing can be on pace. `eating_out` is on pace on some days too.

Scenario 3 (decision 0023): `electronics` is an `over_budget` leak on every day;
`eating_out` (50.00 budget, 41.00 on day 1) is an early-burn leak on every day of the
first half; `alcohol` (a day-1 wine against the monthly beer) is a `spike` on every day.
Side effects: `health` burns in the first half too, and `eating_out` turns over budget
and later a spike in the second half.
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
    17: ("Kebab Haus", _items(("Döner Kebab", "7.50"), ("Cola", "2.50"))),
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
    24: ("Burgerei", _items(("Burger", "11.90"), ("Pommes", "3.90"))),
    26: ("Kebab Haus", _items(("Currywurst", "4.50"), ("Pommes", "3.50"))),
}

# One-offs by month offset from the current month (0 = current, -1 = previous), as
# (day, merchant, items).
ONE_OFFS: dict[int, tuple[tuple[int, str, tuple[SeedItem, ...]], ...]] = {
    0: (
        # Above the electronics budget from day 1: `over` on every day of the month.
        (1, "MediaMarkt", _items(("Kopfhörer", "59.99"))),
        # 9.80 of a 10.00 health budget on day 1 and nothing more: spent stays under the
        # budget, and 9.80 / d * days > 10 for every day d before the last of a 28- to
        # 31-day month, so health is `on_pace_to_overrun` on all of them (on the last day
        # the projection equals the spend and it is `under`).
        (1, "Apotheke am Markt", _items(("Ibuprofen", "4.95"), ("Vitamin", "4.85"))),
        # Eating out, early burn (decision 0023): 41.00 of the 50.00 budget on day 1. The
        # monthly eating-out spend before day 17 adds 3.80 (day 3) and 4.20 (day 10), so
        # it stays within 41.00..49.00, at least 80 % and at most the budget, through the
        # first half of the month; the Kebab Haus and Burgerei visits come after it.
        (
            1,
            "Burgerei",
            _items(("Menü", "24.90"), ("Burger", "11.90"), ("Pommes", "4.20")),
        ),
        # Alcohol spike: 7.99 on day 1 against a median of 4.74 (the day-14 beer) in the
        # two previous months; 7.99 > 1.5 × 4.74 = 7.11, and it only grows afterwards.
        (1, "Weinhandel", _items(("Rotwein", "7.99"))),
    ),
    -1: ((11, "H&M", _items(("Socken", "7.99"), ("Shirt", "12.99"))),),
}

BUDGETS: tuple[SeedBudget, ...] = tuple(
    SeedBudget(category, Decimal(limit))
    for category, limit in (
        ("groceries.fresh", "120.00"),
        ("groceries.staples", "50.00"),
        ("eating_out", "50.00"),
        ("snacks_sweets", "20.00"),
        ("household", "25.00"),
        ("personal_care", "20.00"),
        ("transport", "50.00"),
        ("electronics", "25.00"),
        ("health", "10.00"),
    )
)

GOAL_TARGET = Decimal("1200.00")
GOAL_MONTHS_AHEAD = 5
MONTHLY_INCOME = Decimal("1400.00")


def demo_plan(today: dt.date) -> DemoPlan:
    """Months M-2 and M-1 in full, month M up to `today`; budgets and a goal of 1200 by the
    end of month M+5 with an income of 1400.

    Near the calendar's ends, months before 0001-01 are left out and the goal's month is
    capped at 9999-12, so no `today` makes the plan fail.
    """
    current = Month.of(today)
    expenses: list[SeedExpense] = []
    for offset in (-2, -1, 0):
        month = _shifted_or_none(current, offset)
        if month is None:
            continue
        entries = [(day, merchant, items) for day, (merchant, items) in MONTHLY.items()]
        entries += ONE_OFFS.get(offset, ())
        for day, merchant, items in sorted(entries, key=lambda entry: entry[:2]):
            date = dt.date(month.year, month.month, day)
            if date > today:
                continue
            expenses.append(SeedExpense(merchant=merchant, date=date, line_items=items))
    target = _shifted_or_none(current, GOAL_MONTHS_AHEAD) or LAST_MONTH
    goal = SeedGoal(
        target_amount=GOAL_TARGET,
        target_date=target.last,
        monthly_income=MONTHLY_INCOME,
    )
    return DemoPlan(expenses=tuple(expenses), budgets=BUDGETS, goal=goal)


LAST_MONTH = Month(9999, 12)


def _shifted_or_none(month: Month, by: int) -> Month | None:
    """`month` moved by `by`, or None outside the years 1 to 9999."""
    try:
        return month.shifted(by)
    except ValueError:
        return None


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

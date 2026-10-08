"""Budgets, the month forecast and goal progress (decision 0021; domain-logic.md#budgetpy-f8).

Pure: `today` is a parameter, and the inputs are plain mappings and dataclasses.

- Spend counts confirmed expenses dated in the month, summed per category by the caller;
  `deposit`, `discount` and `uncategorized` (anything that isn't a spending category) are
  dropped here, so the rule is tested in one place.
- Projection: the current month is `spent / today.day * days_in_month`; a past or future
  month is projected as `spent`.
- State: `over` if spent > budget, `on_pace_to_overrun` if projected > budget, else `under`
  (also without a budget, and at exactly the budget).
- Goal: per month and stateless; `months_left = max(1, months to the target, inclusive)`,
  `required = target / months_left`, `saved = income - projected_total`; for a month
  after today's, `saved` and `on_track` are unknown (None).
- Amounts returned are clamped to ±`MONEY_LIMIT`, the API's `Money` range.

Money is `Decimal`, rounded half up to cents.
"""

import calendar
import datetime as dt
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.domain.categories import SPENDING_CATEGORIES
from app.domain.money import cents

SpendState = Literal["under", "on_pace_to_overrun", "over"]

_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


# The largest amount the API's `Money` can carry (max_digits=12, 2 decimals).
MONEY_LIMIT = Decimal("9999999999.99")


def clamp(value: Decimal) -> Decimal:
    """`value` limited to ±`MONEY_LIMIT`, so it fits a response."""
    return max(-MONEY_LIMIT, min(MONEY_LIMIT, value))


@dataclass(frozen=True, order=True)
class Month:
    """A calendar month, `YYYY-MM`."""

    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.year <= 9999 or not 1 <= self.month <= 12:
            raise ValueError(f"not a month: {self.year}-{self.month}")

    @classmethod
    def parse(cls, text: str) -> "Month":
        """`YYYY-MM` with a month from 01 to 12 and a year from 0001; anything else raises
        `ValueError` (the API pattern lets `0000-01` through)."""
        match = _MONTH.fullmatch(text)
        if match is None:
            raise ValueError(f"not a month: {text!r}")
        return cls(int(match[1]), int(match[2]))

    @classmethod
    def of(cls, day: dt.date) -> "Month":
        return cls(day.year, day.month)

    @property
    def first(self) -> dt.date:
        return dt.date(self.year, self.month, 1)

    @property
    def last(self) -> dt.date:
        # Not "next month's first day - 1": 9999-12 has no next month.
        return dt.date(self.year, self.month, self.days)

    @property
    def days(self) -> int:
        return calendar.monthrange(self.year, self.month)[1]

    def shifted(self, months: int) -> "Month":
        """This month moved by `months` (negative goes back); `ValueError` outside the
        years 1 to 9999."""
        index = self.year * 12 + (self.month - 1) + months
        return Month(index // 12, index % 12 + 1)

    def shifted_or_none(self, months: int) -> "Month | None":
        """Like `shifted`, but None outside the years 1 to 9999."""
        try:
            return self.shifted(months)
        except ValueError:
            return None

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


def months_inclusive(start: Month, end: Month) -> int:
    """Months from `start` to `end`, both counted: same month = 1; `end` before `start` ≤ 0."""
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


def spending(spend: Mapping[str, Decimal]) -> dict[str, Decimal]:
    """The raw sums per item category reduced to spend (decision 0021): only spending
    categories, so `deposit`, `discount` and `uncategorized` are dropped; in cents."""
    return {
        category: cents(amount)
        for category, amount in spend.items()
        if category in SPENDING_CATEGORIES
    }


def project(spent: Decimal, month: Month, today: dt.date) -> Decimal:
    """Month-end spend: extrapolated for the current month, `spent` otherwise; in cents."""
    if Month.of(today) != month:
        return cents(spent)
    return cents(spent * month.days / today.day)


def spend_state(spent: Decimal, projected: Decimal, budget: Decimal | None) -> SpendState:
    if budget is None:
        return "under"
    if spent > budget:
        return "over"
    if projected > budget:
        return "on_pace_to_overrun"
    return "under"


@dataclass(frozen=True)
class Goal:
    """The savings goal as stored."""

    target_amount: Decimal
    target_date: dt.date
    monthly_income: Decimal | None


@dataclass(frozen=True)
class GoalProgress:
    target_amount: Decimal
    target_date: dt.date
    saved_this_month: Decimal | None
    required_per_month: Decimal
    on_track: bool | None
    months_left: int


@dataclass(frozen=True)
class CategoryRow:
    category: str
    spent: Decimal
    budget: Decimal | None
    projected: Decimal
    state: SpendState


@dataclass(frozen=True)
class Summary:
    month: Month
    total_spent: Decimal
    total_budget: Decimal | None
    projected_total: Decimal
    categories: tuple[CategoryRow, ...]
    goal: GoalProgress | None


def goal_progress(
    month: Month, goal: Goal, projected_total: Decimal, today: dt.date
) -> GoalProgress:
    """What the summary month needs to save, and whether the forecast saving covers it.

    A target month already past clamps to 1 month left. Without income, or for a month
    after `today`'s (it hasn't started, so there is no spend to judge), `saved_this_month`
    and `on_track` are None. Money is clamped to `MONEY_LIMIT`.
    """
    months_left = max(1, months_inclusive(month, Month.of(goal.target_date)))
    required = cents(goal.target_amount / months_left)
    if goal.monthly_income is None or month > Month.of(today):
        saved, on_track = None, None
    else:
        saved = cents(goal.monthly_income - projected_total)
        on_track = saved >= required
    return GoalProgress(
        target_amount=clamp(cents(goal.target_amount)),
        target_date=goal.target_date,
        saved_this_month=clamp(saved) if saved is not None else None,
        required_per_month=clamp(required),
        on_track=on_track,
        months_left=months_left,
    )


def summarize(
    month: Month,
    today: dt.date,
    spend: Mapping[str, Decimal],
    budgets: Mapping[str, Decimal],
    goal: Goal | None,
) -> Summary:
    """The dashboard for `month`.

    `spend` is the raw sum per item category (non-spending ones are dropped); `budgets`
    maps a category to its monthly limit. Rows are the categories with spend ≠ 0 or a
    budget, by spend descending, then name.

    States, ordering, totals and the goal are computed on the real amounts; only the
    amounts returned are clamped to `MONEY_LIMIT`, so a huge projection can't break the
    response (a current-month projection is up to 31 × the spend).
    """
    spent_by_category = spending(spend)
    limits = {category: cents(limit) for category, limit in budgets.items()}
    names = {name for name, amount in spent_by_category.items() if amount != 0} | set(limits)
    rows = []
    for name in names:
        spent = spent_by_category.get(name, Decimal("0.00"))
        budget = limits.get(name)
        projected = project(spent, month, today)
        rows.append(
            CategoryRow(name, spent, budget, projected, spend_state(spent, projected, budget))
        )
    rows.sort(key=lambda row: (-row.spent, row.category))
    total_spent = sum((row.spent for row in rows), Decimal("0.00"))
    projected_total = sum((row.projected for row in rows), Decimal("0.00"))
    total_budget = sum(limits.values(), Decimal("0.00")) if limits else None
    return Summary(
        month=month,
        total_spent=clamp(total_spent),
        total_budget=clamp(total_budget) if total_budget is not None else None,
        projected_total=clamp(projected_total),
        categories=tuple(
            CategoryRow(
                row.category,
                clamp(row.spent),
                clamp(row.budget) if row.budget is not None else None,
                clamp(row.projected),
                row.state,
            )
            for row in rows
        ),
        goal=goal_progress(month, goal, projected_total, today) if goal is not None else None,
    )

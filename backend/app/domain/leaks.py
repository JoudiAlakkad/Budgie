"""Potential-leak detection (decision 0023; domain-logic.md#leakspy-f9).

Pure: `today` is a parameter, and the inputs are plain mappings.

- Spend is the same as the summary's (decision 0021, `budget.spending`): only spending
  categories get leaks.
- `over_budget`, in any month: `spent > budget`; `amount = spent - budget`.
- `on_pace_to_overrun` (early burn), only in the current month and with a budget:
  `0 < spent <= budget`, `spent >= BURN_USED_MIN * budget` and
  `today.day <= BURN_ELAPSED_MAX * days_in_month`; `amount = projected - budget`.
- `spike`, in any month: at least `SPIKE_MIN_MONTHS` of the `SPIKE_HISTORY_MONTHS`
  previous months have any spend, the category's median over those months is > 0, and
  `spent > SPIKE_FACTOR * median`; `amount = spent - median`.
- Order: over budget, burn, spike; then amount descending, then category.

Every comparison is exact `Decimal` arithmetic. Amounts are rounded half up to cents and
clamped to the API's `Money` range; the explanation is a fixed template with `de-DE`
money and dates and the labels of `frontend/js/categories.js`.
"""

import datetime as dt
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from app.domain.budget import Month, clamp, project, spending
from app.domain.categories import SPENDING_CATEGORIES
from app.domain.money import cents

LeakType = Literal["over_budget", "on_pace_to_overrun", "spike"]

BURN_USED_MIN = Decimal("0.80")
"""Early burn: at least this share of the budget is used ..."""
BURN_ELAPSED_MAX = Decimal("0.50")
"""... while at most this share of the month has passed."""
SPIKE_FACTOR = Decimal("1.5")
"""Spike: the spend is more than this times the median of the history."""
SPIKE_HISTORY_MONTHS = 3
"""The months before the viewed one that make up the spike history."""
SPIKE_MIN_MONTHS = 2
"""History months with any spend needed before a spike can fire."""

CATEGORY_LABELS: dict[str, str] = {
    "groceries.fresh": "Groceries: fresh",
    "groceries.staples": "Groceries: staples",
    "snacks_sweets": "Snacks and sweets",
    "drinks": "Drinks",
    "alcohol": "Alcohol",
    "tobacco": "Tobacco",
    "household": "Household",
    "personal_care": "Personal care",
    "health": "Health",
    "eating_out": "Eating out",
    "transport": "Transport",
    "clothing": "Clothing",
    "electronics": "Electronics",
    "other": "Other",
    "deposit": "Deposit (Pfand)",
    "discount": "Discount",
    "uncategorized": "Uncategorised",
}
"""The UI's names; equal to `CATEGORY_LABELS` in `frontend/js/categories.js` (a test)."""

_TYPE_ORDER: dict[str, int] = {"over_budget": 0, "on_pace_to_overrun": 1, "spike": 2}
_ZERO = Decimal("0.00")


@dataclass(frozen=True)
class Leak:
    """One potential leak; `merchant` is always None in F09."""

    type: LeakType
    category: str
    amount: Decimal
    explanation: str
    merchant: str | None = None


# ---------------------------------------------------------------- formats


def format_money(value: Decimal) -> str:
    """`de-DE` currency with a plain space: `1.234,50 €`, `-0,75 €`."""
    rounded = cents(value)
    sign = "-" if rounded < 0 else ""
    whole, fraction = f"{abs(rounded):.2f}".split(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return f"{sign}{'.'.join(groups)},{fraction} €"


def format_date(day: dt.date) -> str:
    """`de-DE` medium: `14.10.2026`."""
    return f"{day.day:02d}.{day.month:02d}.{day.year:04d}"


def format_percent(value: Decimal) -> str:
    """A whole number, rounded half up: `89`."""
    return str(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category)


# ---------------------------------------------------------------- helpers


def median(values: Sequence[Decimal]) -> Decimal:
    """The middle value, or the mean of the two middle ones; `ValueError` when empty."""
    if not values:
        raise ValueError("median of nothing")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def counted_history(history: Iterable[Mapping[str, Decimal]]) -> list[dict[str, Decimal]]:
    """The history months that count: those with any spend ≠ 0 after the 0021 filter, so
    months before the user started don't count as 0. Each as its spend per category."""
    months = [spending(month) for month in history]
    return [month for month in months if any(amount != 0 for amount in month.values())]


def run_out_day(spent: Decimal, budget: Decimal, today: dt.date) -> int:
    """`ceil(budget × today.day / spent)`: the day of the month the budget is used up at
    the current daily rate. Needs `spent > 0`."""
    return math.ceil(budget * today.day / spent)


# ---------------------------------------------------------------- the rules


def over_budget(category: str, spent: Decimal, budget: Decimal | None) -> Leak | None:
    if budget is None or spent <= budget:
        return None
    over = spent - budget
    return Leak(
        type="over_budget",
        category=category,
        amount=clamp(cents(over)),
        explanation=(
            f"{label(category)}: {format_money(spent)} spent of a {format_money(budget)} "
            f"budget, {format_money(over)} over."
        ),
    )


def early_burn(
    category: str,
    month: Month,
    today: dt.date,
    spent: Decimal,
    budget: Decimal | None,
    visits: int,
) -> Leak | None:
    """`on_pace_to_overrun`: most of the budget gone in the first half of the current month."""
    if budget is None or Month.of(today) != month:
        return None
    if not 0 < spent <= budget:
        return None
    if spent < BURN_USED_MIN * budget or today.day > BURN_ELAPSED_MAX * month.days:
        return None
    projected = project(spent, month, today)
    extra = projected - budget
    used = format_percent(spent / budget * 100)
    days_left = month.days - today.day
    day = run_out_day(spent, budget, today)
    if day == today.day:
        pace = (
            "The budget is used up today, and at this pace the month ends at about "
            f"{format_money(projected)} (+{format_money(extra)})."
        )
    else:
        runs_out = dt.date(month.year, month.month, day)
        pace = (
            f"At this pace it runs out around {format_date(runs_out)} and the month ends "
            f"at about {format_money(projected)} (+{format_money(extra)})."
        )
    text = (
        f"{label(category)}: you've used {used} % of your {format_money(budget)} budget "
        f"({format_money(spent)}) by {format_date(today)}, with {days_left} days left. {pace}"
    )
    if visits > 0:
        text += f" {visits} visit(s) so far, about {format_money(spent / visits)} each."
    return Leak(
        type="on_pace_to_overrun",
        category=category,
        amount=clamp(cents(extra)),
        explanation=text,
    )


def spike(category: str, spent: Decimal, counted: Sequence[Mapping[str, Decimal]]) -> Leak | None:
    """`spike`: more than `SPIKE_FACTOR` × the median of the counted history months."""
    if len(counted) < SPIKE_MIN_MONTHS:
        return None
    typical = median([month.get(category, _ZERO) for month in counted])
    if typical <= 0 or spent <= SPIKE_FACTOR * typical:
        return None
    rise = format_percent((spent / typical - 1) * 100)
    return Leak(
        type="spike",
        category=category,
        amount=clamp(cents(spent - typical)),
        explanation=(
            f"{label(category)}: {format_money(spent)} this month, +{rise} % vs. your median "
            f"of {format_money(typical)} over the last {len(counted)} months."
        ),
    )


def detect_leaks(
    month: Month,
    today: dt.date,
    spend: Mapping[str, Decimal],
    budgets: Mapping[str, Decimal],
    history: Iterable[Mapping[str, Decimal]],
    visits: Mapping[str, int],
) -> list[Leak]:
    """The potential leaks of `month`, in the 0023 order.

    `spend` is the month's raw sum per item category and `history` the same for each of
    the (up to `SPIKE_HISTORY_MONTHS`) previous months; non-spending categories are
    dropped. `budgets` maps a category to its monthly limit; `visits` counts the
    confirmed expenses of the month with an item in the category (only the current
    month's are used).
    """
    spent_by_category = spending(spend)
    limits = {category: cents(limit) for category, limit in budgets.items()}
    counted = counted_history(history)
    leaks: list[Leak] = []
    for category in sorted(set(spent_by_category) | set(limits)):
        if category not in SPENDING_CATEGORIES:
            continue
        spent = spent_by_category.get(category, _ZERO)
        budget = limits.get(category)
        found = (
            over_budget(category, spent, budget),
            early_burn(category, month, today, spent, budget, visits.get(category, 0)),
            spike(category, spent, counted),
        )
        leaks.extend(leak for leak in found if leak is not None)
    leaks.sort(key=lambda leak: (_TYPE_ORDER[leak.type], -leak.amount, leak.category))
    return leaks

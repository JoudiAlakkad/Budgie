"""`domain/budget.py`: months, the forecast, spend states and goal progress (decision 0021)."""

import datetime as dt
from decimal import Decimal
from typing import get_args

import pytest

from app.api.schemas import SpendingCategory
from app.domain.budget import (
    MONEY_LIMIT,
    SPENDING_CATEGORIES,
    Goal,
    Month,
    months_inclusive,
    project,
    spend_state,
    summarize,
)

D = Decimal
OCT = Month(2026, 10)
MID_OCT = dt.date(2026, 10, 15)


# ---------------------------------------------------------------- Month


@pytest.mark.parametrize(
    ("text", "month", "first", "last", "days"),
    [
        ("2026-10", Month(2026, 10), dt.date(2026, 10, 1), dt.date(2026, 10, 31), 31),
        ("2026-02", Month(2026, 2), dt.date(2026, 2, 1), dt.date(2026, 2, 28), 28),
        ("2024-02", Month(2024, 2), dt.date(2024, 2, 1), dt.date(2024, 2, 29), 29),
        ("2026-04", Month(2026, 4), dt.date(2026, 4, 1), dt.date(2026, 4, 30), 30),
        ("2026-12", Month(2026, 12), dt.date(2026, 12, 1), dt.date(2026, 12, 31), 31),
    ],
)
def test_month_parse_and_bounds(
    text: str, month: Month, first: dt.date, last: dt.date, days: int
) -> None:
    parsed = Month.parse(text)

    assert parsed == month
    assert (parsed.first, parsed.last, parsed.days) == (first, last, days)
    assert str(parsed) == text
    assert Month.of(last) == month


@pytest.mark.parametrize("text", ["2026-13", "2026-00", "2026-1", "26-10", "2026-10-01", ""])
def test_month_parse_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        Month.parse(text)


@pytest.mark.parametrize(
    ("month", "by", "expected"),
    [
        (Month(2026, 10), 0, Month(2026, 10)),
        (Month(2026, 10), 3, Month(2027, 1)),
        (Month(2026, 1), -1, Month(2025, 12)),
        (Month(2026, 10), -14, Month(2025, 8)),
    ],
)
def test_month_shifted(month: Month, by: int, expected: Month) -> None:
    assert month.shifted(by) == expected


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (Month(2026, 10), Month(2026, 10), 1),
        (Month(2026, 10), Month(2027, 3), 6),
        (Month(2026, 12), Month(2027, 1), 2),
        (Month(2026, 10), Month(2026, 9), 0),
    ],
)
def test_months_inclusive(start: Month, end: Month, expected: int) -> None:
    assert months_inclusive(start, end) == expected


# ---------------------------------------------------------------- projection


@pytest.mark.parametrize(
    ("spent", "month", "today", "expected"),
    [
        # current month: spent / today.day * days
        ("10.00", OCT, dt.date(2026, 10, 1), "310.00"),  # day 1
        ("31.30", OCT, MID_OCT, "64.69"),  # day 15: 64.686… rounds half up
        ("40.00", OCT, dt.date(2026, 10, 31), "40.00"),  # last day: no extrapolation
        ("1.00", OCT, dt.date(2026, 10, 3), "10.33"),  # 10.333…
        ("0.10", Month(2026, 2), dt.date(2026, 2, 8), "0.35"),  # 0.35 exactly
        ("0.00", OCT, MID_OCT, "0.00"),
        # past and future months: projected = spent
        ("123.45", Month(2026, 9), MID_OCT, "123.45"),
        ("123.45", Month(2026, 11), MID_OCT, "123.45"),
        ("50.00", Month(2025, 10), MID_OCT, "50.00"),  # same month, another year
    ],
)
def test_project(spent: str, month: Month, today: dt.date, expected: str) -> None:
    assert project(D(spent), month, today) == D(expected)


# ---------------------------------------------------------------- state


@pytest.mark.parametrize(
    ("spent", "projected", "budget", "expected"),
    [
        ("10", "20", None, "under"),  # no budget
        ("10", "20", "30", "under"),
        ("10", "30", "30", "under"),  # projected exactly the budget
        ("10", "30.01", "30", "on_pace_to_overrun"),
        ("30", "30", "30", "under"),  # spent exactly the budget
        ("30", "60", "30", "on_pace_to_overrun"),  # spent equal, projection above
        ("30.01", "30.01", "30", "over"),
        ("60", "120", "30", "over"),  # over wins over on pace
    ],
)
def test_spend_state(spent: str, projected: str, budget: str | None, expected: str) -> None:
    limit = D(budget) if budget is not None else None
    assert spend_state(D(spent), D(projected), limit) == expected


# ---------------------------------------------------------------- summarize


def test_rows_drop_non_spending_and_order_by_spend_then_name() -> None:
    spend = {
        "groceries.fresh": D("20.00"),
        "drinks": D("20.00"),
        "eating_out": D("31.30"),
        "deposit": D("2.25"),
        "discount": D("-1.00"),
        "uncategorized": D("5.00"),
        "alcohol": D("0.00"),  # no spend, no budget: not listed
    }
    budgets = {"eating_out": D("60"), "health": D("15")}

    summary = summarize(OCT, MID_OCT, spend, budgets, None)

    assert [row.category for row in summary.categories] == [
        "eating_out",
        "drinks",
        "groceries.fresh",
        "health",  # budget only, spent 0
    ]
    health = summary.categories[-1]
    assert (health.spent, health.projected, health.state) == (D("0.00"), D("0.00"), "under")
    assert summary.total_spent == D("71.30")
    assert summary.total_budget == D("75.00")
    assert summary.goal is None


def test_negative_spend_is_listed() -> None:
    summary = summarize(Month(2026, 9), MID_OCT, {"other": D("-3.00")}, {}, None)

    assert [(row.category, row.spent) for row in summary.categories] == [("other", D("-3.00"))]


def test_states_in_the_current_month() -> None:
    spend = {"electronics": D("59.99"), "eating_out": D("31.30"), "groceries.fresh": D("21.60")}
    budgets = {"electronics": D("25"), "eating_out": D("60"), "groceries.fresh": D("120")}

    rows = {r.category: r for r in summarize(OCT, MID_OCT, spend, budgets, None).categories}

    assert rows["electronics"].state == "over"
    assert rows["eating_out"].state == "on_pace_to_overrun"
    assert rows["eating_out"].projected == D("64.69")
    assert rows["groceries.fresh"].state == "under"


def test_a_past_month_is_never_on_pace() -> None:
    spend = {"eating_out": D("31.30")}
    summary = summarize(Month(2026, 9), MID_OCT, spend, {"eating_out": D("40")}, None)

    [row] = summary.categories
    assert (row.projected, row.state) == (D("31.30"), "under")
    assert summary.projected_total == summary.total_spent


def test_projected_total_is_the_sum_of_rounded_rows() -> None:
    # Each row: 1.00 / 3 * 31 = 10.333… -> 10.33; the unrounded sum would be 20.67.
    spend = {"drinks": D("1.00"), "alcohol": D("1.00")}

    summary = summarize(OCT, dt.date(2026, 10, 3), spend, {}, None)

    assert [row.projected for row in summary.categories] == [D("10.33"), D("10.33")]
    assert summary.projected_total == D("20.66")


def test_no_budgets_means_total_budget_null() -> None:
    summary = summarize(OCT, MID_OCT, {"drinks": D("5")}, {}, None)

    assert summary.total_budget is None
    assert summary.categories[0].budget is None


def test_empty_month() -> None:
    summary = summarize(OCT, MID_OCT, {}, {}, None)

    assert summary.categories == ()
    assert (summary.total_spent, summary.projected_total) == (D("0.00"), D("0.00"))
    assert summary.month == OCT


def test_amounts_are_rounded_to_cents() -> None:
    summary = summarize(Month(2026, 9), MID_OCT, {"drinks": D("1.005")}, {"drinks": D("2.5")}, None)

    [row] = summary.categories
    assert (row.spent, row.budget) == (D("1.01"), D("2.50"))


# ---------------------------------------------------------------- goal


def goal(target: str = "1200", on: dt.date = dt.date(2027, 3, 31), income: str | None = "1400"):
    return Goal(D(target), on, D(income) if income is not None else None)


@pytest.mark.parametrize(
    ("the_goal", "month", "spend", "required", "saved", "on_track"),
    [
        # Oct 2026 .. Mar 2027 inclusive = 6 months
        (goal(), OCT, {}, "200.00", "1400.00", True),
        # saved uses the projection: 600 by the 15th -> 1240.00 projected -> 160 < 200
        (goal(), OCT, {"other": D("600")}, "200.00", "160.00", False),
        # a past month: saved = income - spent; Sep .. Mar = 7 months
        (goal(), Month(2026, 9), {"other": D("1028.57")}, "171.43", "371.43", True),
        # saved exactly the requirement is on track
        (goal(), Month(2026, 9), {"other": D("1228.57")}, "171.43", "171.43", True),
        (goal(), Month(2026, 9), {"other": D("1228.58")}, "171.43", "171.42", False),
        # rounding: 1000 / 3
        (goal("1000", dt.date(2026, 12, 1)), OCT, {}, "333.33", "1400.00", True),
        # target in the summary month: 1 month
        (goal("500", dt.date(2026, 10, 20)), OCT, {}, "500.00", "1400.00", True),
        # target passed: clamped to 1 month
        (goal("500", dt.date(2026, 8, 31)), OCT, {}, "500.00", "1400.00", True),
        # spending above income: negative saving
        (goal(income="100"), Month(2026, 9), {"other": D("150")}, "171.43", "-50.00", False),
        # no income: saved and on_track unknown
        (goal(income=None), OCT, {"other": D("10")}, "200.00", None, None),
        # a month after today's hasn't started: unknown, not "income - 0 = on track"
        (goal(), Month(2026, 11), {}, "240.00", None, None),  # Nov .. Mar = 5 months
        (goal(), Month(2027, 3), {"other": D("5")}, "1200.00", None, None),
        (goal(), Month(2027, 9), {}, "1200.00", None, None),  # after the target too
    ],
)
def test_goal_progress(
    the_goal: Goal,
    month: Month,
    spend: dict[str, Decimal],
    required: str,
    saved: str | None,
    on_track: bool | None,
) -> None:
    progress = summarize(month, MID_OCT, spend, {}, the_goal).goal

    assert progress is not None
    assert progress.required_per_month == D(required)
    assert progress.saved_this_month == (D(saved) if saved is not None else None)
    assert progress.on_track is on_track
    assert (progress.target_amount, progress.target_date) == (
        the_goal.target_amount,
        the_goal.target_date,
    )


def test_saved_exactly_required_boundary() -> None:
    # On the last day the projection equals the spend: 1400 - 1200 = 200 = 1200 / 6.
    progress = summarize(OCT, dt.date(2026, 10, 31), {"other": D("1200")}, {}, goal()).goal

    assert progress is not None
    assert (progress.saved_this_month, progress.required_per_month) == (D("200.00"), D("200.00"))
    assert progress.on_track is True


@pytest.mark.parametrize(("year", "month"), [(2026, 0), (2026, 13), (0, 1), (10000, 1)])
def test_month_rejects_impossible_values(year: int, month: int) -> None:
    with pytest.raises(ValueError):
        Month(year, month)


def test_spending_categories_match_the_contract() -> None:
    assert set(get_args(SpendingCategory)) == SPENDING_CATEGORIES


@pytest.mark.parametrize(
    ("text", "last"), [("9999-12", dt.date(9999, 12, 31)), ("0001-01", dt.date(1, 1, 31))]
)
def test_the_calendar_ends(text: str, last: dt.date) -> None:
    month = Month.parse(text)

    assert (month.last, month.days) == (last, 31)
    assert project(D("1"), month, MID_OCT) == D("1.00")


def test_year_zero_matches_the_api_pattern_but_is_not_a_month() -> None:
    with pytest.raises(ValueError):
        Month.parse("0000-01")


def test_a_far_target_rounds_the_requirement_to_zero() -> None:
    """Known behaviour, pinned: 400 over 95,927 months (Oct 2026 .. Dec 9999) is 0.0042,
    which rounds to 0.00, so any saving counts as on track. The logic is left as decision
    0021 states it."""
    far = Goal(D("400"), dt.date(9999, 12, 31), D("0"))

    progress = summarize(OCT, MID_OCT, {}, {}, far).goal

    assert progress is not None
    assert progress.required_per_month == D("0.00")
    assert progress.saved_this_month == D("0.00")
    assert progress.on_track is True


# ---------------------------------------------------------------- money limit

LIMIT = D("9999999999.99")


def test_the_limit_is_the_largest_response_money() -> None:
    assert MONEY_LIMIT == LIMIT


def test_a_huge_projection_is_clamped_but_keeps_its_state() -> None:
    # Day 1 of a 31-day month: 5,000,000,000 x 31 would not fit the API's Money.
    spend = {"electronics": D("5000000000.00"), "other": D("4999999999.99")}
    budgets = {"electronics": D("9999999999.99"), "health": D("9999999999.99")}

    summary = summarize(OCT, dt.date(2026, 10, 1), spend, budgets, goal())

    rows = {row.category: row for row in summary.categories}
    assert rows["electronics"].projected == LIMIT
    assert rows["electronics"].spent == D("5000000000.00")
    assert rows["electronics"].state == "on_pace_to_overrun"  # judged on the real amount
    assert summary.projected_total == LIMIT
    assert summary.total_spent == LIMIT  # 9,999,999,999.99 fits exactly
    assert summary.total_budget == LIMIT  # twice the limit, clamped
    assert summary.goal is not None
    assert summary.goal.saved_this_month == -LIMIT
    assert summary.goal.on_track is False


def test_total_spent_above_the_limit_is_clamped() -> None:
    spend = {"electronics": D("9000000000"), "other": D("9000000000")}

    summary = summarize(Month(2026, 9), MID_OCT, spend, {}, None)

    assert summary.total_spent == summary.projected_total == LIMIT
    assert [row.spent for row in summary.categories] == [D("9000000000.00")] * 2


def test_negative_amounts_are_clamped_too() -> None:
    summary = summarize(OCT, dt.date(2026, 10, 1), {"other": D("-5000000000")}, {}, None)

    assert summary.categories[0].projected == summary.projected_total == -LIMIT

"""`domain/leaks.py`: the potential-leak rules of decision 0023."""

import datetime as dt
from decimal import Decimal

import pytest

from app.domain import leaks
from app.domain.budget import MONEY_LIMIT, Month, spending
from app.domain.categories import SPENDING_CATEGORIES
from app.domain.leaks import (
    BURN_ELAPSED_MAX,
    BURN_USED_MIN,
    SPIKE_FACTOR,
    SPIKE_HISTORY_MONTHS,
    SPIKE_MIN_MONTHS,
    Leak,
    counted_history,
    detect_leaks,
    early_burn,
    format_date,
    format_money,
    format_percent,
    median,
    over_budget,
    run_out_day,
    spike,
)

D = Decimal
OCT = Month(2026, 10)
NOV = Month(2026, 11)
FEB = Month(2026, 2)
LEAP_FEB = Month(2024, 2)
MID_OCT = dt.date(2026, 10, 14)
# Two history months with some spend, so a spike can fire.
HISTORY = [{"drinks": D("10")}, {"drinks": D("10")}]


def detect(
    spend: dict[str, str],
    budgets: dict[str, str] | None = None,
    *,
    month: Month = OCT,
    today: dt.date = MID_OCT,
    history: list[dict[str, Decimal]] | None = None,
    visits: dict[str, int] | None = None,
) -> list[Leak]:
    return detect_leaks(
        month,
        today,
        {category: D(amount) for category, amount in spend.items()},
        {category: D(limit) for category, limit in (budgets or {}).items()},
        history or [],
        visits or {},
    )


def kinds(found: list[Leak]) -> list[tuple[str, str]]:
    return [(leak.type, leak.category) for leak in found]


# ---------------------------------------------------------------- constants and labels


def test_the_thresholds_are_those_of_0023() -> None:
    assert (D("0.80"), D("0.50"), D("1.5")) == (BURN_USED_MIN, BURN_ELAPSED_MAX, SPIKE_FACTOR)
    assert (SPIKE_HISTORY_MONTHS, SPIKE_MIN_MONTHS) == (3, 2)


def test_every_spending_category_has_a_label() -> None:
    assert set(leaks.CATEGORY_LABELS) >= SPENDING_CATEGORIES


# ---------------------------------------------------------------- the motivating case


def test_the_motivating_case_eating_out_at_89_percent_by_mid_october() -> None:
    found = detect({"eating_out": "44.50"}, {"eating_out": "50"}, visits={"eating_out": 6})

    assert found == [
        Leak(
            type="on_pace_to_overrun",
            category="eating_out",
            amount=D("48.54"),
            explanation=(
                "Eating out: you've used 89 % of your 50,00 € budget (44,50 €) by "
                "14.10.2026, with 17 days left. At this pace it runs out around 16.10.2026 "
                "and the month ends at about 98,54 € (+48,54 €). 6 visit(s) so far, about "
                "7,42 € each."
            ),
            merchant=None,
        )
    ]


# ---------------------------------------------------------------- early burn


@pytest.mark.parametrize(
    ("spent", "fires"),
    [
        ("40.00", True),  # exactly 80 %
        ("39.99", False),  # just below
        ("44.50", True),
        ("50.00", True),  # exactly the budget: burn, not over
        ("50.01", False),  # over the budget: over_budget instead
        ("0.00", False),
        ("-5.00", False),
    ],
)
def test_burn_needs_80_percent_used_and_at_most_the_budget(spent: str, fires: bool) -> None:
    found = detect({"eating_out": spent}, {"eating_out": "50"})

    assert (("on_pace_to_overrun", "eating_out") in kinds(found)) is fires


@pytest.mark.parametrize(
    ("today", "fires"),
    [
        (dt.date(2026, 11, 15), True),  # 15 / 30 = exactly half
        (dt.date(2026, 11, 16), False),
        (dt.date(2026, 10, 15), True),  # 15 / 31
        (dt.date(2026, 10, 16), False),  # 16 / 31
        (dt.date(2026, 2, 14), True),  # 14 / 28 = exactly half
        (dt.date(2026, 2, 15), False),  # 15 / 28
        (dt.date(2024, 2, 14), True),  # 14 / 29
        (dt.date(2024, 2, 15), False),  # 15 / 29
        (dt.date(2026, 10, 1), True),
    ],
    ids=str,
)
def test_burn_only_in_the_first_half_of_the_month(today: dt.date, fires: bool) -> None:
    found = detect({"eating_out": "45"}, {"eating_out": "50"}, month=Month.of(today), today=today)

    assert (kinds(found) == [("on_pace_to_overrun", "eating_out")]) is fires


@pytest.mark.parametrize("month", [Month(2026, 9), Month(2026, 11)], ids=str)
def test_a_past_or_future_month_never_burns(month: Month) -> None:
    assert detect({"eating_out": "45"}, {"eating_out": "50"}, month=month) == []


def test_no_budget_no_burn_and_no_over() -> None:
    assert detect({"eating_out": "4500"}) == []


def test_over_budget_is_never_also_burn() -> None:
    found = detect({"eating_out": "62.40"}, {"eating_out": "50"})

    assert kinds(found) == [("over_budget", "eating_out")]


def test_burn_amount_is_the_projection_over_the_budget() -> None:
    (leak,) = detect({"health": "9.80"}, {"health": "10"}, today=dt.date(2026, 10, 1))

    assert leak.amount == D("293.80")  # 9.80 × 31 − 10


def test_run_out_today_says_the_budget_is_used_up() -> None:
    (leak,) = detect({"eating_out": "50"}, {"eating_out": "50"}, visits={"eating_out": 2})

    assert leak.explanation == (
        "Eating out: you've used 100 % of your 50,00 € budget (50,00 €) by 14.10.2026, "
        "with 17 days left. The budget is used up today, and at this pace the month ends "
        "at about 110,71 € (+60,71 €). 2 visit(s) so far, about 25,00 € each."
    )
    assert "runs out around" not in leak.explanation


@pytest.mark.parametrize(
    ("visits", "tail"),
    [
        (1, " 1 visit(s) so far, about 44,50 € each."),
        (2, " 2 visit(s) so far, about 22,25 € each."),
        (0, " (+48,54 €)."),  # no visit count known: no visit sentence
    ],
)
def test_the_visit_text_is_visit_s_for_every_count(visits: int, tail: str) -> None:
    (leak,) = detect({"eating_out": "44.50"}, {"eating_out": "50"}, visits={"eating_out": visits})

    assert leak.explanation.endswith(tail)


@pytest.mark.parametrize(
    ("spent", "budget", "day", "expected"),
    [
        ("44.50", "50", 14, 16),
        ("50", "50", 14, 14),  # the budget is used up today
        ("40", "50", 1, 2),  # 1.25 → 2
        ("40", "50", 4, 5),
        ("40", "50", 15, 19),  # 18.75 → 19
    ],
)
def test_run_out_day(spent: str, budget: str, day: int, expected: int) -> None:
    assert run_out_day(D(spent), D(budget), dt.date(2026, 10, day)) == expected


def test_early_burn_helper_needs_a_budget() -> None:
    assert early_burn("eating_out", OCT, MID_OCT, D("45"), None, 3) is None


# ---------------------------------------------------------------- over budget


@pytest.mark.parametrize(
    ("spent", "budget", "amount"),
    [("62.40", "50", "12.40"), ("50.01", "50", "0.01"), ("50", "50", None), ("10", None, None)],
)
def test_over_budget(spent: str, budget: str | None, amount: str | None) -> None:
    leak = over_budget("eating_out", D(spent), D(budget) if budget else None)

    assert (leak.amount if leak else None) == (D(amount) if amount else None)


def test_over_budget_text_and_any_month() -> None:
    for month in (Month(2026, 9), OCT, Month(2026, 11)):
        found = detect({"eating_out": "62.40"}, {"eating_out": "50"}, month=month)

        assert [leak.explanation for leak in found] == [
            "Eating out: 62,40 € spent of a 50,00 € budget, 12,40 € over."
        ]


# ---------------------------------------------------------------- spike


@pytest.mark.parametrize(
    ("history", "spent", "fires"),
    [
        ([{"snacks_sweets": D("20")}, {"snacks_sweets": D("20")}], "30.01", True),
        ([{"snacks_sweets": D("20")}, {"snacks_sweets": D("20")}], "30.00", False),  # 1.5×
        ([{"snacks_sweets": D("20")}], "100", False),  # 1 counted month
        ([], "100", False),  # none
        ([{"snacks_sweets": D("20")}, {}, {}], "100", False),  # empty months don't count
        ([{"drinks": D("5")}, {"drinks": D("5")}], "100", False),  # median 0
        ([{"drinks": D("5")}, {"snacks_sweets": D("8")}], "6.01", True),  # median 4
        ([{"drinks": D("5")}, {"snacks_sweets": D("8")}], "6.00", False),
        # deposits, discounts and uncategorised items don't make a month count
        ([{"snacks_sweets": D("20")}, {"deposit": D("3"), "uncategorized": D("4")}], "100", False),
    ],
)
def test_spike_rules(history: list[dict[str, Decimal]], spent: str, fires: bool) -> None:
    found = detect({"snacks_sweets": spent}, history=history)

    assert (kinds(found) == [("spike", "snacks_sweets")]) is fires


@pytest.mark.parametrize(
    ("count", "phrase"), [(2, "over the last 2 months."), (3, "over the last 3 months.")]
)
def test_spike_text_counts_the_counted_months(count: int, phrase: str) -> None:
    history = [{"snacks_sweets": D("23.75")}] * count

    (leak,) = detect({"snacks_sweets": "38"}, history=history)

    assert leak.amount == D("14.25")
    assert leak.explanation == (
        f"Snacks and sweets: 38,00 € this month, +60 % vs. your median of 23,75 € {phrase}"
    )


def test_spike_median_of_three_months_and_amount() -> None:
    # The middle month counts (it has drinks) and gives alcohol 0: median of 0, 4.74, 10.
    history = [{"alcohol": D("4.74")}, {"drinks": D("1")}, {"alcohol": D("10")}]

    (leak,) = detect({"alcohol": "7.99"}, history=history)

    assert leak.amount == D("3.25")  # 7.99 − median 4.74
    assert "+69 %" in leak.explanation


def test_spike_in_a_past_month() -> None:
    found = detect({"alcohol": "10"}, month=Month(2026, 8), history=[{"alcohol": D("4")}] * 2)

    assert kinds(found) == [("spike", "alcohol")]


def test_spike_helper_with_too_little_history() -> None:
    assert spike("alcohol", D("100"), [{"alcohol": D("1")}]) is None


# ---------------------------------------------------------------- helpers


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["3"], "3"),
        (["1", "3"], "2"),
        (["0", "4.75"], "2.375"),
        (["5", "1", "3"], "3"),
        (["0", "0", "7"], "0"),
    ],
)
def test_median(values: list[str], expected: str) -> None:
    assert median([D(v) for v in values]) == D(expected)


def test_median_of_nothing_is_an_error() -> None:
    with pytest.raises(ValueError):
        median([])


def test_counted_history_keeps_months_with_spend() -> None:
    history = [
        {"drinks": D("1")},
        {},
        {"deposit": D("2"), "discount": D("-1"), "uncategorized": D("3")},
        {"drinks": D("0")},
        {"drinks": D("-1")},
    ]

    assert counted_history(history) == [{"drinks": D("1.00")}, {"drinks": D("-1.00")}]


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("44.5", "44,50 €"),
        ("0", "0,00 €"),
        ("7.416666", "7,42 €"),
        ("0.005", "0,01 €"),
        ("999.99", "999,99 €"),
        ("1234.5", "1.234,50 €"),
        ("1234567.891", "1.234.567,89 €"),
        ("-0.75", "-0,75 €"),
    ],
)
def test_format_money(value: str, text: str) -> None:
    assert format_money(D(value)) == text
    assert " " not in format_money(D(value))  # a plain space, not the NBSP


@pytest.mark.parametrize(
    ("day", "text"),
    [
        (dt.date(2026, 10, 14), "14.10.2026"),
        (dt.date(2026, 1, 3), "03.01.2026"),
    ],
)
def test_format_date(day: dt.date, text: str) -> None:
    assert format_date(day) == text


@pytest.mark.parametrize(
    ("value", "text"), [("89", "89"), ("88.5", "89"), ("88.49", "88"), ("60.0", "60"), ("0.5", "1")]
)
def test_format_percent(value: str, text: str) -> None:
    assert format_percent(D(value)) == text


# ---------------------------------------------------------------- spend, order and limits


def test_deposit_discount_and_uncategorised_never_get_leaks() -> None:
    found = detect(
        {"deposit": "500", "discount": "500", "uncategorized": "500"},
        {"deposit": "1"},  # even with a budget, which the API can't store
        history=[{"deposit": D("1"), "uncategorized": D("1")}] * 3,
    )

    assert found == []


def test_spend_uses_the_0021_filter() -> None:
    assert spending({"drinks": D("1.005"), "deposit": D("2"), "uncategorized": D("3")}) == {
        "drinks": D("1.01")
    }


def test_over_budget_and_spike_together() -> None:
    found = detect(
        {"eating_out": "120"},
        {"eating_out": "50"},
        month=Month(2026, 9),
        history=[{"eating_out": D("40")}] * 3,
    )

    assert kinds(found) == [("over_budget", "eating_out"), ("spike", "eating_out")]
    assert [leak.amount for leak in found] == [D("70.00"), D("80.00")]


def test_order_by_type_then_amount_then_category() -> None:
    history = [{"alcohol": D("2"), "drinks": D("2"), "tobacco": D("2")}] * 2
    found = detect(
        {
            "electronics": "60",  # over by 35
            "clothing": "35",  # over by 10
            "household": "35",  # over by 10
            "eating_out": "44.50",  # burn
            "health": "9.80",  # burn
            "alcohol": "10",  # spike 8
            "drinks": "5",  # spike 3
            "tobacco": "5",  # spike 3
        },
        {
            "electronics": "25",
            "clothing": "25",
            "household": "25",
            "eating_out": "50",
            "health": "10",
        },
        history=history,
    )

    assert kinds(found) == [
        ("over_budget", "electronics"),
        ("over_budget", "clothing"),
        ("over_budget", "household"),
        ("on_pace_to_overrun", "eating_out"),  # 48.54
        ("on_pace_to_overrun", "health"),  # 11.70
        ("spike", "alcohol"),
        ("spike", "drinks"),
        ("spike", "tobacco"),
    ]
    assert all(leak.merchant is None for leak in found)


def test_amounts_are_clamped_to_the_money_limit() -> None:
    huge = "50000000000"

    found = detect(
        {"electronics": huge, "alcohol": huge},
        {"electronics": "1"},
        history=[{"alcohol": D("1")}] * 2,
    )

    assert [leak.amount for leak in found] == [MONEY_LIMIT, MONEY_LIMIT]


def test_burn_amount_is_clamped() -> None:
    (leak,) = detect(
        {"electronics": "9000000000"},
        {"electronics": "9000000000"},
        today=dt.date(2026, 10, 1),
    )

    assert leak.type == "on_pace_to_overrun"
    assert leak.amount == MONEY_LIMIT


def test_nothing_spent_nothing_found() -> None:
    assert detect({}, {"eating_out": "50"}, history=HISTORY) == []

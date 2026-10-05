"""Validation rules (docs/wiki/backend/domain-logic.md#validationpy-f4): the parsers, the
item-sum check, the date checks and the currency check. One table per rule."""

from datetime import date
from decimal import Decimal

import pytest

from app.domain.facts import Flag, ItemFacts, ReceiptFacts
from app.domain.validation import (
    normalize_currency,
    parse_date,
    parse_number,
    validate,
    years_before,
)

TODAY = date(2026, 10, 5)
MAX_FLOAT = 1.7976931348623157e308


def facts(**overrides: object) -> ReceiptFacts:
    """A receipt that passes every validation rule; override one field per case."""
    values: dict[str, object] = {
        "merchant": "REWE",
        "date": "2026-10-01",
        "currency": "EUR",
        "subtotal": None,
        "tax": None,
        "total": Decimal("3.00"),
        "items": (
            ItemFacts("MILCH", Decimal("1.00"), "groceries.staples"),
            ItemFacts("BROT", Decimal("2.00"), "groceries.staples"),
        ),
        "unreadable_fields": (),
    }
    values.update(overrides)
    return ReceiptFacts(**values)  # type: ignore[arg-type]


def items(*amounts: str) -> tuple[ItemFacts, ...]:
    return tuple(ItemFacts(f"ITEM {i}", Decimal(a), "other") for i, a in enumerate(amounts))


def float_item(amount: float) -> ItemFacts:
    """An item whose amount came from a float, e.g. Decimal(2.98) == 2.97999..."""
    return ItemFacts("FLOAT", Decimal(amount), "other")


def codes(flags: list[Flag]) -> list[str]:
    return [flag.code for flag in flags]


# --- parse_number -------------------------------------------------------------------

NUMBERS = [
    ("1,99", "1.99"),
    ("1.234,56", "1234.56"),
    ("-0,50", "-0.50"),
    ("1.99", "1.99"),
    ("1234", "1234"),
    ("0", "0"),
    ("-3", "-3"),
    ("12,5", "12.5"),
    ("1234,56", "1234.56"),
    ("1.234.567,89", "1234567.89"),
    ("  2,49 ", "2.49"),
    # a single "." followed by exactly 3 digits is a German thousands separator
    ("1.234", "1234"),
    ("12.345", "12345"),
    ("-1.234", "-1234"),
    ("1.234.567", "1234567"),
    # otherwise, without a comma, "." is the decimal separator
    ("12.5", "12.5"),
    ("1.2345", "1.2345"),
    ("1234.56", "1234.56"),
    ("-0.50", "-0.50"),
    ("0,50", "0.50"),
    ("0.5", "0.5"),
    ("0,123", "0.123"),
]


@pytest.mark.parametrize(("text", "expected"), NUMBERS)
def test_parse_number(text: str, expected: str) -> None:
    assert parse_number(text) == Decimal(expected)


NOT_NUMBERS = [
    "",
    "   ",
    "abc",
    "1,99 EUR",
    "€1,99",
    "1.234.5",  # ambiguous: neither thousands groups nor one decimal point
    "1234.567",  # 3 digits after the dot but no valid thousands grouping
    "12.34,5.6",
    "1,2,3",
    "1.23,45",  # "." before a comma must separate groups of 3
    ".5",
    "5.",
    ",5",
    "5,",
    "1 234,56",
    "+1,99",
    "--1",
    "1e3",
    "NaN",
    "١٢٣",  # non-ASCII digits
    # no leading zero in the first group of a grouped number
    "0.123,45",
    "000.000",
    "01.234",
    "-0.123,45",
    "0.123",
]


@pytest.mark.parametrize("text", NOT_NUMBERS)
def test_parse_number_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        parse_number(text)


# --- normalize_currency -------------------------------------------------------------

CURRENCIES = [
    ("€", "EUR"),
    (" € ", "EUR"),
    ("EUR", "EUR"),
    ("eur", "EUR"),
    ("Eur", "EUR"),
    (" EUR\n", "EUR"),
    ("USD", "USD"),
    ("chf", "CHF"),
    ("", None),
    ("  ", None),
    ("$", None),
    ("EURO", None),
    ("EU", None),
    ("E1R", None),
    ("€€", None),
    ("ÄÖÜ", None),  # three letters, but not ASCII
    ("E U R", None),
]


@pytest.mark.parametrize(("text", "expected"), CURRENCIES)
def test_normalize_currency(text: str, expected: str | None) -> None:
    assert normalize_currency(text) == expected


# --- parse_date ---------------------------------------------------------------------

DATES = [
    ("2026-09-22", date(2026, 9, 22)),
    ("22.09.2026", date(2026, 9, 22)),
    (" 2026-09-22 ", date(2026, 9, 22)),
    ("29.02.2024", date(2024, 2, 29)),
    ("2024-02-29", date(2024, 2, 29)),
    ("2025-02-29", None),  # not a leap year
    ("31.04.2026", None),
    ("2026-13-01", None),
    ("00.01.2026", None),
    ("2026/09/22", None),
    ("22/09/2026", None),
    ("22.9.2026", None),
    ("2026-9-22", None),
    ("22.09.26", None),
    ("09-22-2026", None),
    ("2026-09-22T10:00", None),
    ("", None),
    ("yesterday", None),
]


@pytest.mark.parametrize(("text", "expected"), DATES)
def test_parse_date(text: str, expected: date | None) -> None:
    assert parse_date(text) == expected


YEARS_BEFORE = [
    (date(2026, 10, 5), date(2024, 10, 5)),
    (date(2028, 2, 29), date(2026, 2, 28)),
    (date(2026, 3, 1), date(2024, 3, 1)),
]


@pytest.mark.parametrize(("day", "expected"), YEARS_BEFORE)
def test_years_before(day: date, expected: date) -> None:
    assert years_before(day, 2) == expected


# --- sum_mismatch -------------------------------------------------------------------

# (case, overrides, expected flags: None, one (field, message), or a list of them)
SUMS = [
    ("matches total", {"items": items("1.00", "2.00"), "total": Decimal("3.00")}, None),
    ("0.02 under total passes", {"items": items("2.98"), "total": Decimal("3.00")}, None),
    ("0.02 over total passes", {"items": items("3.02"), "total": Decimal("3.00")}, None),
    (
        "0.03 under total",
        {"items": items("2.97"), "total": Decimal("3.00")},
        ("total", "Items sum to 2.97 but total is 3.00."),
    ),
    (
        "0.03 over total",
        {"items": items("3.03"), "total": Decimal("3.00")},
        ("total", "Items sum to 3.03 but total is 3.00."),
    ),
    (
        "brief example",
        {"items": items("10.00", "2.40"), "total": Decimal("14.40")},
        ("total", "Items sum to 12.40 but total is 14.40."),
    ),
    (
        "subtotal off, total matches",
        {"items": items("3.00"), "subtotal": Decimal("2.50"), "total": Decimal("3.00")},
        ("subtotal", "Items sum to 3.00 but subtotal is 2.50."),
    ),
    (
        "subtotal matches, total off",
        {"items": items("3.00"), "subtotal": Decimal("3.00"), "total": Decimal("99.00")},
        ("total", "Items sum to 3.00 but total is 99.00."),
    ),
    (
        "both off: two flags, subtotal first",
        {"items": items("3.00"), "subtotal": Decimal("2.50"), "total": Decimal("4.00")},
        [
            ("subtotal", "Items sum to 3.00 but subtotal is 2.50."),
            ("total", "Items sum to 3.00 but total is 4.00."),
        ],
    ),
    (
        "both match within 0.02",
        {"items": items("3.00"), "subtotal": Decimal("3.02"), "total": Decimal("2.98")},
        None,
    ),
    (
        "net subtotal plus tax (US style) is flagged on the total",
        {
            "items": items("23.00"),
            "subtotal": Decimal("23.00"),
            "tax": Decimal("2.00"),
            "total": Decimal("25.00"),
        },
        ("total", "Items sum to 23.00 but total is 25.00."),
    ),
    (
        "subtotal 0.02 off passes",
        {"items": items("3.00"), "subtotal": Decimal("3.02"), "total": None},
        None,
    ),
    ("no items", {"items": (), "total": Decimal("3.00")}, None),
    ("no subtotal and no total", {"items": items("1.00"), "total": None}, None),
    (
        "negative deposit and discount lines count",
        {"items": items("1.00", "-0.25", "2.00"), "total": Decimal("2.75")},
        None,
    ),
    (
        "rounded half up to cents in the message",
        {"items": items("1.005"), "total": Decimal("2")},
        ("total", "Items sum to 1.01 but total is 2.00."),
    ),
    # amounts built from floats are compared in cents, not as 2.97999...
    ("float-built item passes", {"items": (float_item(2.98),), "total": Decimal("3.00")}, None),
    (
        "float-built items sum passes",
        {"items": (float_item(1.0), float_item(1.98)), "total": Decimal("3.00")},
        None,
    ),
    (
        "float-built target passes",
        {"items": items("2.98"), "total": Decimal(3.0)},
        None,
    ),
    (
        "float-built item 0.03 off fails",
        {"items": (float_item(2.97),), "total": Decimal("3.00")},
        ("total", "Items sum to 2.97 but total is 3.00."),
    ),
    (
        "subtotal 0.03 off",
        {"items": items("3.00"), "subtotal": Decimal("3.03"), "total": Decimal("3.00")},
        ("subtotal", "Items sum to 3.00 but subtotal is 3.03."),
    ),
    # huge finite amounts (a JSON float reaches 1.8e308) are flagged, never raise;
    # expected values come from exact ints, not from Decimal arithmetic at 28 digits
    (
        "huge total vs a normal item",
        {"items": items("1.00"), "total": Decimal(1e30)},
        ("total", f"Items sum to 1.00 but total is {int(1e30)}.00."),
    ),
    (
        "huge item vs a normal total",
        {"items": (float_item(1e300),), "total": Decimal("1.00")},
        ("total", f"Items sum to {int(1e300)}.00 but total is 1.00."),
    ),
    (
        "huge items in several lines",
        {"items": (float_item(1e300), float_item(1e300)), "total": Decimal("1.00")},
        ("total", f"Items sum to {2 * int(1e300)}.00 but total is 1.00."),
    ),
    (
        "huge subtotal vs huge items, 1 apart in the last digit",
        {"items": (float_item(1e300),), "subtotal": Decimal(int(1e300) + 1), "total": None},
        ("subtotal", f"Items sum to {int(1e300)}.00 but subtotal is {int(1e300) + 1}.00."),
    ),
    (
        "largest float on both sides matches",
        {"items": (float_item(MAX_FLOAT),), "total": Decimal(MAX_FLOAT)},
        None,
    ),
]


@pytest.mark.parametrize(
    ("overrides", "expected"), [case[1:] for case in SUMS], ids=[case[0] for case in SUMS]
)
def test_sum_mismatch(
    overrides: dict, expected: tuple[str, str] | list[tuple[str, str]] | None
) -> None:
    flags = validate(facts(**overrides), TODAY)

    if expected is None:
        expected = []
    elif isinstance(expected, tuple):
        expected = [expected]
    assert flags == [Flag(field, "sum_mismatch", message) for field, message in expected]


def test_no_tax_check() -> None:
    """German receipts print VAT as included: the spike's TEDi run must pass."""
    tedi = facts(
        items=items("3.10"), subtotal=Decimal("3.10"), tax=Decimal("0.49"), total=Decimal("3.10")
    )

    assert validate(tedi, TODAY) == []


# --- date checks --------------------------------------------------------------------

# (date text, today, expected code or None)
DATE_CHECKS = [
    ("2026-10-05", TODAY, None),  # today
    ("05.10.2026", TODAY, None),
    ("2026-10-06", TODAY, "date_in_future"),  # tomorrow
    ("06.10.2026", TODAY, "date_in_future"),
    ("2027-01-01", TODAY, "date_in_future"),
    ("2024-10-05", TODAY, None),  # exactly 2 years before
    ("2024-10-04", TODAY, "date_too_old"),  # one day more
    ("04.10.2024", TODAY, "date_too_old"),
    ("2023-10-26", TODAY, "date_too_old"),
    # 29 Feb: two years before is 28 Feb
    ("2026-02-28", date(2028, 2, 29), None),
    ("2026-02-27", date(2028, 2, 29), "date_too_old"),
    ("2028-03-01", date(2028, 2, 29), "date_in_future"),
    # a receipt dated 29 Feb: still in range on 28 Feb two years later, too old on 1 Mar
    ("2024-02-29", date(2026, 2, 28), None),
    ("2024-02-28", date(2026, 2, 28), None),
    ("2024-02-27", date(2026, 2, 28), "date_too_old"),
    ("2024-02-29", date(2026, 3, 1), "date_too_old"),
    ("2026-02-31", TODAY, "date_unparseable"),
    ("2026/10/01", TODAY, "date_unparseable"),
    ("gestern", TODAY, "date_unparseable"),
    (None, TODAY, None),  # missing is a review flag, not a validation flag
    ("", TODAY, None),
    ("   ", TODAY, None),
]


@pytest.mark.parametrize(("text", "today", "expected"), DATE_CHECKS)
def test_date_checks(text: str | None, today: date, expected: str | None) -> None:
    flags = validate(facts(date=text), today)

    assert codes(flags) == ([expected] if expected else [])
    assert all(flag.field == "date" for flag in flags)


DATE_MESSAGES = [
    ("2026-10-06", "The date 2026-10-06 is in the future."),
    ("04.10.2024", "The date 2024-10-04 is more than 2 years ago."),
    ("31.02.2026", 'The date "31.02.2026" is not a valid date in YYYY-MM-DD or DD.MM.YYYY.'),
]


@pytest.mark.parametrize(("text", "message"), DATE_MESSAGES)
def test_date_messages(text: str, message: str) -> None:
    assert [flag.message for flag in validate(facts(date=text), TODAY)] == [message]


# --- currency_unknown ---------------------------------------------------------------

CURRENCY_CHECKS = [
    ("EUR", None),
    ("€", None),
    ("eur", None),
    ("USD", None),
    (None, None),
    ("", None),
    ("  ", None),
    ("$", "currency_unknown"),
    ("Euro", "currency_unknown"),
    ("12", "currency_unknown"),
]


@pytest.mark.parametrize(("text", "expected"), CURRENCY_CHECKS)
def test_currency_unknown(text: str | None, expected: str | None) -> None:
    flags = validate(facts(currency=text), TODAY)

    assert codes(flags) == ([expected] if expected else [])
    assert all(flag.field == "currency" for flag in flags)


def test_currency_message() -> None:
    [flag] = validate(facts(currency=" $ "), TODAY)

    assert flag.message == 'The currency "$" is not recognised.'


def test_validate_order_is_sum_date_currency() -> None:
    bad = facts(items=items("1.00"), total=Decimal("5.00"), date="2030-01-01", currency="$$")

    assert codes(validate(bad, TODAY)) == ["sum_mismatch", "date_in_future", "currency_unknown"]

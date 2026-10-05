"""Deterministic checks of a receipt (docs/wiki/backend/domain-logic.md#validationpy-f4).

Parsers for German numbers, currencies and dates, and `validate`: the item-sum check
(tolerance 0.02) and the date and currency checks. There is no tax check: German
receipts print VAT as included (decision 0008, amendment F04).
"""

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext

from app.domain.facts import Flag, ReceiptFacts, is_blank

SUM_TOLERANCE = Decimal("0.02")
CENT = Decimal("0.01")
# Sums are rounded in a context wide enough for any float-built amount: a float's
# integer part has at most 309 digits, and 100 items add at most 3 more. The default
# 28 digits would make `quantize` raise InvalidOperation on e.g. Decimal(1e30).
SUM_PRECISION = 400
MAX_AGE_YEARS = 2

# parse_number, in this order (ASCII digits only, optional leading "-", no spaces):
# 1. With a comma: the comma is the decimal separator, and any "." is a thousands
#    separator between groups of exactly 3 digits: `1,99`, `1.234,56`, `-0,50`.
# 2. No comma, dots between 3-digit groups after 1-3 leading digits: thousands,
#    `1.234` -> 1234, `1.234.567` -> 1234567. A single "." followed by exactly 3 digits
#    is therefore always thousands, never a decimal.
# 3. No comma, one "." not followed by exactly 3 digits: decimal, `1.99`, `12.5`.
# 4. Digits only: `1234`.
# The first group of a grouped number has no leading zero, so `0.123,45`, `000.000` and
# `0.123` are rejected, while `0,50` and `0.5` parse.
# Anything else raises ValueError: empty, letters, `1.234.5`, `1234.567`, `1,2,3`, `.5`.
_COMMA_DECIMAL = re.compile(r"-?(?:[1-9]\d{0,2}(?:\.\d{3})+|\d+),\d+", re.ASCII)
_DOT_THOUSANDS = re.compile(r"-?[1-9]\d{0,2}(?:\.\d{3})+", re.ASCII)
_DOT_DECIMAL = re.compile(r"-?\d+\.(?!\d{3}$)\d+", re.ASCII)
_INTEGER = re.compile(r"-?\d+", re.ASCII)

_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})", re.ASCII)
_GERMAN_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})", re.ASCII)
_CURRENCY_CODE = re.compile(r"[A-Za-z]{3}", re.ASCII)
_EURO = {"€", "EUR"}


def parse_number(text: str) -> Decimal:
    """A German or plain number as a Decimal; raises ValueError if it is neither."""
    value = text.strip()
    if _COMMA_DECIMAL.fullmatch(value):
        return Decimal(value.replace(".", "").replace(",", "."))
    if _DOT_THOUSANDS.fullmatch(value):
        return Decimal(value.replace(".", ""))
    if _DOT_DECIMAL.fullmatch(value) or _INTEGER.fullmatch(value):
        return Decimal(value)
    raise ValueError(f"not a number: {text!r}")


def normalize_currency(text: str) -> str | None:
    """`€`/`EUR`/`eur` -> `EUR`, another 3-letter code uppercased, anything else None."""
    value = text.strip()
    if value.upper() in _EURO:
        return "EUR"
    if _CURRENCY_CODE.fullmatch(value):
        return value.upper()
    return None


def parse_date(text: str) -> date | None:
    """`YYYY-MM-DD` or `DD.MM.YYYY` as a real calendar date, else None."""
    value = text.strip()
    if match := _ISO_DATE.fullmatch(value):
        year, month, day = match.groups()
    elif match := _GERMAN_DATE.fullmatch(value):
        day, month, year = match.groups()
    else:
        return None
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def years_before(day: date, years: int) -> date:
    """The same calendar date `years` earlier; 29 Feb falls back to 28 Feb."""
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


def check_sum(facts: ReceiptFacts) -> list[Flag]:
    """`sum_mismatch` if the items are off the subtotal (or the total) by more than 0.02."""
    if not facts.items:
        return []
    if facts.subtotal is not None:
        field, target = "subtotal", facts.subtotal
    elif facts.total is not None:
        field, target = "total", facts.total
    else:
        return []
    # Rounded to cents first: an amount built from a float (Decimal(2.98) is
    # 2.979999...) must not tip the comparison past the tolerance. Everything runs in
    # the wide context, so a huge finite amount is flagged instead of raising.
    with localcontext(prec=SUM_PRECISION):
        items_sum = _cents(sum((item.amount for item in facts.items), Decimal(0)))
        target = _cents(target)
        if abs(items_sum - target) <= SUM_TOLERANCE:
            return []
        message = f"Items sum to {_money(items_sum)} but {field} is {_money(target)}."
    return [Flag(field, "sum_mismatch", message)]


def check_date(text: str | None, today: date) -> list[Flag]:
    """`date_unparseable`, `date_in_future` or `date_too_old`; a missing date is not checked."""
    if is_blank(text):
        return []
    parsed = parse_date(text)
    if parsed is None:
        message = f'The date "{text.strip()}" is not a valid date in YYYY-MM-DD or DD.MM.YYYY.'
        return [Flag("date", "date_unparseable", message)]
    if parsed > today:
        message = f"The date {parsed.isoformat()} is in the future."
        return [Flag("date", "date_in_future", message)]
    if parsed < years_before(today, MAX_AGE_YEARS):
        message = f"The date {parsed.isoformat()} is more than {MAX_AGE_YEARS} years ago."
        return [Flag("date", "date_too_old", message)]
    return []


def check_currency(text: str | None) -> list[Flag]:
    """`currency_unknown` if a currency is present but not recognised."""
    if is_blank(text) or normalize_currency(text) is not None:
        return []
    message = f'The currency "{text.strip()}" is not recognised.'
    return [Flag("currency", "currency_unknown", message)]


def validate(facts: ReceiptFacts, today: date) -> list[Flag]:
    """The arithmetic, date and currency flags, in that order."""
    return [*check_sum(facts), *check_date(facts.date, today), *check_currency(facts.currency)]

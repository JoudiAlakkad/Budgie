"""The duplicate rule (decision 0020; docs/wiki/backend/domain-logic.md#duplicatespy-f7)."""

import re
import time
from datetime import date
from decimal import Decimal

import pytest

from app.domain import duplicates
from app.domain.duplicates import (
    MAX_MERCHANT_CHARS,
    ExpenseKey,
    duplicate_flag,
    is_duplicate,
    normalize_merchant,
)
from app.domain.facts import Flag

DAY = date(2026, 9, 17)


def key(
    id_: int | None = 1,
    merchant: str | None = "REWE Markt GmbH",
    day: date | None = DAY,
    total: str | None = "15.15",
) -> ExpenseKey:
    return ExpenseKey(id_, merchant, day, Decimal(total) if total is not None else None)


@pytest.mark.parametrize(
    ("merchant", "normalised"),
    [
        ("REWE Markt GmbH", "rewe markt"),
        ("rewe markt", "rewe markt"),
        ("REWE  Markt\tGmbH.", "rewe markt"),
        ("Edeka Müller e.K.", "edeka mueller"),
        ("Edeka Müller e. K.", "edeka mueller"),
        ("Bäckerei Schmidt e.Kfm.", "baeckerei schmidt"),
        ("ALDI SÜD GmbH & Co. KG", "aldi sued"),
        ("dm-drogerie markt GmbH + Co. KG", "dm drogerie markt"),
        ("Lidl Vertriebs-GmbH & Co. KG", "lidl vertriebs"),
        ("Kaufland AG", "kaufland"),
        ("Getränke OHG", "getraenke"),
        ("Café Crème UG", "cafe creme"),
        ("GmbH", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_merchant(merchant: str | None, normalised: str) -> None:
    assert normalize_merchant(merchant) == normalised


@pytest.mark.parametrize(
    ("other", "expected"),
    [
        (key(2), True),
        (key(2, merchant="rewe markt"), True),
        (key(2, merchant="REWE MARKT GMBH"), True),
        (key(2, total="15.16"), True),  # +0.01
        (key(2, total="15.14"), True),  # -0.01
        (key(2, total="15.155"), True),  # rounds to 15.16
        (key(2, total="15.17"), False),
        (key(2, total="15.13"), False),
        (key(2, total="15.144"), True),  # rounds to 15.14, a cent off
        (key(2, total="15.134"), False),  # rounds to 15.13
        (key(2, merchant="REWE City"), False),
        (key(2, day=date(2026, 9, 18)), False),
        (key(2, merchant=None), False),
        (key(2, merchant="  "), False),
        (key(2, merchant="GmbH"), False),  # nothing left to compare
        (key(2, day=None), False),
        (key(2, total=None), False),
        (key(1), False),  # the expense itself
    ],
)
def test_is_duplicate(other: ExpenseKey, expected: bool) -> None:
    assert is_duplicate(key(1), other) is expected


@pytest.mark.parametrize(
    "candidate",
    [key(None, merchant=None), key(None, day=None), key(None, total=None), key(None, merchant="")],
)
def test_a_candidate_missing_a_field_is_never_a_duplicate(candidate: ExpenseKey) -> None:
    others = [key(2, merchant=None), key(3, day=None), key(4, total=None), key(5)]

    assert duplicate_flag(candidate, others) is None


def test_a_candidate_without_an_id_matches_any_id() -> None:
    assert is_duplicate(key(None), key(1)) is True


def test_duplicate_flag_names_the_first_match() -> None:
    others = [key(4, merchant="Aldi"), key(7, total="15.16"), key(9)]

    flag = duplicate_flag(key(None), others)

    assert flag == Flag(
        None,
        "possible_duplicate",
        "Looks like a duplicate of expense 7 (REWE Markt GmbH, 2026-09-17, 15.16).",
    )


def test_no_others_no_flag() -> None:
    assert duplicate_flag(key(None), []) is None


def test_the_expense_itself_is_skipped() -> None:
    assert duplicate_flag(key(3), [key(3)]) is None
    assert duplicate_flag(key(3), [key(3), key(8)]) is not None


# ---------------------------------------------------------------- slow-regex guard

SLOW_S = 2.0


@pytest.mark.parametrize("unit", ["e.", "e. k", "e.k.", ".", " ", "a-", "gmbh ", "ä", "&"])
def test_normalize_merchant_finishes_on_hostile_input(unit: str) -> None:
    text = (unit * (65_536 // len(unit) + 1))[:65_536]
    bounded = text[:MAX_MERCHANT_CHARS]
    patterns = [v for v in vars(duplicates).values() if isinstance(v, re.Pattern)]

    start = time.perf_counter()
    normalize_merchant(text)
    for pattern in patterns:
        for _ in pattern.finditer(bounded):
            pass
    elapsed = time.perf_counter() - start

    assert elapsed < SLOW_S

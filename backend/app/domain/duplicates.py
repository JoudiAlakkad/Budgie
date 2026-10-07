"""Duplicate receipts (decision 0020; docs/wiki/backend/domain-logic.md#duplicatespy-f7).

An expense is a likely duplicate of another one when the normalised merchant, the date
and the total (within 0.01) match. It only gets a `possible_duplicate` flag on
`field: null`; nothing is deleted. The caller passes the other expenses (every expense
but the one being assessed, confirmed or not).
"""

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.domain.categorize import fold
from app.domain.facts import Flag, is_blank

# Merchant lines are short; anything after this is ignored (bounds the work).
MAX_MERCHANT_CHARS = 1024
TOTAL_TOLERANCE = Decimal("0.01")
CENT = Decimal("0.01")

# Legal-form words dropped from a merchant: `REWE Markt GmbH` == `rewe markt`.
LEGAL_SUFFIXES = frozenset({"gmbh", "mbh", "ag", "kg", "kgaa", "ohg", "ug", "se", "co", "ek"})
# `e.K.` / `e. K.` / `e.Kfm.` before punctuation is removed, so `e` and `k` aren't split.
_EK = re.compile(r"(?<!\w)e\.[ ]?k(?:fm)?\.?(?!\w)")
_NON_WORD = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class ExpenseKey:
    """What the duplicate rule compares; `id` None for an expense not stored yet."""

    id: int | None
    merchant: str | None
    date: dt.date | None
    total: Decimal | None


def normalize_merchant(text: str | None) -> str:
    """Lowercase, umlauts folded, punctuation and legal suffixes dropped, spaces collapsed."""
    if text is None:
        return ""
    folded = _EK.sub(" ", fold(text[:MAX_MERCHANT_CHARS]))
    words = [w for w in _NON_WORD.split(folded) if w and w not in LEGAL_SUFFIXES]
    return " ".join(words)


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def is_duplicate(candidate: ExpenseKey, other: ExpenseKey) -> bool:
    """Same normalised merchant, same date, totals within 0.01 (both rounded to cents).

    False if any of the three is missing on either side, or for the expense itself.
    """
    if candidate.id is not None and candidate.id == other.id:
        return False
    if candidate.date is None or other.date is None or candidate.date != other.date:
        return False
    if candidate.total is None or other.total is None:
        return False
    if abs(_cents(candidate.total) - _cents(other.total)) > TOTAL_TOLERANCE:
        return False
    if is_blank(candidate.merchant) or is_blank(other.merchant):
        return False
    merchant = normalize_merchant(candidate.merchant)
    return bool(merchant) and merchant == normalize_merchant(other.merchant)


def duplicate_flag(candidate: ExpenseKey, others: Sequence[ExpenseKey]) -> Flag | None:
    """A `possible_duplicate` flag naming the first matching other expense, or None."""
    for other in others:
        if is_duplicate(candidate, other):
            assert other.date is not None and other.total is not None
            message = (
                f"Looks like a duplicate of expense {other.id} "
                f"({other.merchant}, {other.date.isoformat()}, {_cents(other.total)})."
            )
            return Flag(None, "possible_duplicate", message)
    return None

"""Rule-based review status (decision 0008; docs/wiki/backend/domain-logic.md#confidencepy-f4).

No score or probability exists: the status is one of three strings, decided by the
flags, and every flag carries a sentence a user can read. How this answers the
uncertainty criterion is in docs/wiki/backend/uncertainty.md.
"""

from collections.abc import Sequence
from datetime import date
from typing import Literal

from app.domain.duplicates import ExpenseKey, duplicate_flag
from app.domain.facts import Flag, ReceiptFacts, is_blank
from app.domain.validation import parse_date, validate

# Mirrors `app.api.schemas.ReviewStatus`; a test checks they match.
ReviewStatus = Literal["accepted", "needs_review", "rejected"]

_FIELD_NAMES = {"line_items": "line items", "payment_method": "payment method"}


def _field_name(key: str) -> str:
    return _FIELD_NAMES.get(key, key)


def review_flags(facts: ReceiptFacts) -> list[Flag]:
    """Missing-field, `unreadable` and `uncategorized_item` flags, in that order.

    An `unreadable` key that is also missing (merchant, date, total) gets only the
    missing_* flag.
    """
    flags: list[Flag] = []
    missing: set[str] = set()
    if is_blank(facts.merchant):
        missing.add("merchant")
        flags.append(Flag("merchant", "missing_merchant", "The merchant is missing."))
    if is_blank(facts.date):
        missing.add("date")
        flags.append(Flag("date", "missing_date", "The date is missing."))
    if facts.total is None:
        missing.add("total")
        flags.append(Flag("total", "missing_total", "The total is missing."))
    # One flag per key (the schema lets the model repeat a key), and none for a key that
    # already has its missing_* flag, so each field gets one message.
    for key in dict.fromkeys(facts.unreadable_fields):
        if key in missing:
            continue
        message = f"The {_field_name(key)} could not be read from the receipt; please check it."
        flags.append(Flag(key, "unreadable", message))
    for index, item in enumerate(facts.items):
        if not item.is_categorized:
            message = f'The item "{item.description}" has no category yet.'
            flags.append(Flag(f"line_items[{index}]", "uncategorized_item", message))
    return flags


def review_status(facts: ReceiptFacts, flags: list[Flag]) -> ReviewStatus:
    """`rejected` if nothing usable was extracted, else `needs_review` on any flag."""
    if facts.total is None and not facts.items:
        return "rejected"
    if flags:
        return "needs_review"
    return "accepted"


def is_plausible_receipt(facts: ReceiptFacts) -> bool:
    """False iff there is no merchant, no total and at most one item (decision 0015)."""
    return not (is_blank(facts.merchant) and facts.total is None and len(facts.items) <= 1)


def assess(
    facts: ReceiptFacts, today: date, others: Sequence[ExpenseKey] = ()
) -> tuple[ReviewStatus, list[Flag]]:
    """The review status and every flag: validation flags, then review flags, then
    `possible_duplicate` if `others` (every other expense) holds a match (decision 0020)."""
    flags = [*validate(facts, today), *review_flags(facts)]
    candidate = ExpenseKey(
        id=None,
        merchant=facts.merchant,
        date=parse_date(facts.date) if facts.date is not None else None,
        total=facts.total,
    )
    duplicate = duplicate_flag(candidate, others)
    if duplicate is not None:
        flags.append(duplicate)
    return review_status(facts, flags), flags

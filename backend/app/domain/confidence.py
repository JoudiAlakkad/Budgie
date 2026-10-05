"""Rule-based review status (decision 0008; docs/wiki/backend/domain-logic.md#confidencepy-f4).

No score or probability exists: the status is one of three strings, decided by the
flags, and every flag carries a sentence a user can read. How this answers the
uncertainty criterion is in docs/wiki/backend/uncertainty.md.
"""

from datetime import date
from typing import Literal

from app.domain.facts import Flag, ReceiptFacts, is_blank
from app.domain.validation import validate

# Mirrors `app.api.schemas.ReviewStatus`; a test checks they match.
ReviewStatus = Literal["accepted", "needs_review", "rejected"]

_FIELD_NAMES = {"line_items": "line items", "payment_method": "payment method"}


def _field_name(key: str) -> str:
    return _FIELD_NAMES.get(key, key)


def review_flags(facts: ReceiptFacts) -> list[Flag]:
    """Missing-field, `unreadable` and `uncategorized_item` flags, in that order."""
    flags: list[Flag] = []
    if is_blank(facts.merchant):
        flags.append(Flag("merchant", "missing_merchant", "The merchant is missing."))
    if is_blank(facts.date):
        flags.append(Flag("date", "missing_date", "The date is missing."))
    if facts.total is None:
        flags.append(Flag("total", "missing_total", "The total is missing."))
    # One flag per key; the schema lets the model repeat a key.
    for key in dict.fromkeys(facts.unreadable_fields):
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


def assess(facts: ReceiptFacts, today: date) -> tuple[ReviewStatus, list[Flag]]:
    """The review status and every flag: validation flags first, then review flags."""
    flags = [*validate(facts, today), *review_flags(facts)]
    return review_status(facts, flags), flags

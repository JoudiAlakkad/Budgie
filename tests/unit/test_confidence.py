"""Review flags, review status, the plausibility rule and `assess`
(docs/wiki/backend/domain-logic.md#confidencepy-f4, decisions 0008 and 0015)."""

import json
import re
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import get_args

import pytest

from app.ai.schema import ReceiptExtraction
from app.api import schemas
from app.domain import confidence
from app.domain.confidence import assess, is_plausible_receipt, review_flags, review_status
from app.domain.facts import FlagCode, ItemFacts, ReceiptFacts
from tests.recorded import load_case

DOMAIN_LOGIC_MD = Path(__file__).resolve().parents[2] / "docs/wiki/backend/domain-logic.md"
TODAY = date(2026, 10, 5)
LATER_FEATURE_CODES = {"possible_duplicate"}  # F7


def item(description: str = "MILCH", amount: str = "1.00", category: str | None = "other"):
    return ItemFacts(description, Decimal(amount), category)


def facts(**overrides: object) -> ReceiptFacts:
    """A complete receipt with no flags at all; override one field per case."""
    values: dict[str, object] = {
        "merchant": "REWE",
        "date": "2026-10-01",
        "currency": "EUR",
        "subtotal": None,
        "tax": None,
        "total": Decimal("3.00"),
        "items": (item("MILCH", "1.00"), item("BROT", "2.00")),
        "unreadable_fields": (),
    }
    values.update(overrides)
    return ReceiptFacts(**values)  # type: ignore[arg-type]


def pairs(flags) -> list[tuple[str | None, str]]:
    return [(flag.field, flag.code) for flag in flags]


# --- review_flags -------------------------------------------------------------------

REVIEW_FLAGS = [
    ("complete", {}, []),
    ("merchant None", {"merchant": None}, [("merchant", "missing_merchant")]),
    ("merchant blank", {"merchant": "  "}, [("merchant", "missing_merchant")]),
    ("merchant empty", {"merchant": ""}, [("merchant", "missing_merchant")]),
    ("date None", {"date": None}, [("date", "missing_date")]),
    ("date blank", {"date": " "}, [("date", "missing_date")]),
    ("total None", {"total": None}, [("total", "missing_total")]),
    ("total zero is present", {"total": Decimal("0")}, []),
    ("currency None is no review flag", {"currency": None}, []),
    ("subtotal None is no review flag", {"subtotal": None}, []),
    ("one unreadable", {"unreadable_fields": ("tax",)}, [("tax", "unreadable")]),
    (
        "unreadable keeps the model's order",
        {"unreadable_fields": ("total", "merchant", "line_items")},
        [("total", "unreadable"), ("merchant", "unreadable"), ("line_items", "unreadable")],
    ),
    (
        "a missing field listed as unreadable gets only the missing flag",
        {
            "merchant": None,
            "date": " ",
            "total": None,
            "unreadable_fields": ("merchant", "date", "total", "tax"),
        },
        [
            ("merchant", "missing_merchant"),
            ("date", "missing_date"),
            ("total", "missing_total"),
            ("tax", "unreadable"),
        ],
    ),
    (
        "a present field listed as unreadable is flagged",
        {"unreadable_fields": ("merchant", "date", "total")},
        [("merchant", "unreadable"), ("date", "unreadable"), ("total", "unreadable")],
    ),
    (
        "a repeated unreadable key flags once",
        {"unreadable_fields": ("tax", "tax")},
        [("tax", "unreadable")],
    ),
    (
        "uncategorised None",
        {"items": (item(category="other"), item(category=None))},
        [("line_items[1]", "uncategorized_item")],
    ),
    (
        "uncategorised by name",
        {"items": (item(category="uncategorized"), item(category="drinks"))},
        [("line_items[0]", "uncategorized_item")],
    ),
    (
        "uncategorised by name, any case and spacing",
        {"items": (item(category=" Uncategorized "), item(category="UNCATEGORIZED"))},
        [("line_items[0]", "uncategorized_item"), ("line_items[1]", "uncategorized_item")],
    ),
    (
        "uncategorised blank",
        {"items": (item(category="  "),)},
        [("line_items[0]", "uncategorized_item")],
    ),
    (
        "uncategorised empty string",
        {"items": (item(category=""),)},
        [("line_items[0]", "uncategorized_item")],
    ),
    (
        "every item uncategorised",
        {"items": (item(category=None), item(category=None))},
        [("line_items[0]", "uncategorized_item"), ("line_items[1]", "uncategorized_item")],
    ),
    (
        "all at once, in a fixed order",
        {
            "merchant": None,
            "date": None,
            "total": None,
            "unreadable_fields": ("total",),
            "items": (item(category=None),),
        },
        [
            ("merchant", "missing_merchant"),
            ("date", "missing_date"),
            ("total", "missing_total"),
            ("line_items[0]", "uncategorized_item"),
        ],
    ),
]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [case[1:] for case in REVIEW_FLAGS],
    ids=[case[0] for case in REVIEW_FLAGS],
)
def test_review_flags(overrides: dict, expected: list) -> None:
    assert pairs(review_flags(facts(**overrides))) == expected


MESSAGES = [
    ({"merchant": None}, "The merchant is missing."),
    ({"date": None}, "The date is missing."),
    ({"total": None}, "The total is missing."),
    (
        {"unreadable_fields": ("line_items",)},
        "The line items could not be read from the receipt; please check it.",
    ),
    (
        {"unreadable_fields": ("payment_method",)},
        "The payment method could not be read from the receipt; please check it.",
    ),
    ({"items": (item("BROT", category=None),)}, 'The item "BROT" has no category yet.'),
]


@pytest.mark.parametrize(("overrides", "message"), MESSAGES)
def test_review_messages_are_sentences(overrides: dict, message: str) -> None:
    [flag] = review_flags(facts(**overrides))

    assert flag.message == message


# --- review_status ------------------------------------------------------------------

STATUSES = [
    ("no flags", {}, "accepted"),
    ("one flag", {"merchant": None}, "needs_review"),
    ("total but no items", {"items": ()}, "accepted"),
    ("items but no total", {"total": None}, "needs_review"),
    ("no total and no items", {"total": None, "items": ()}, "rejected"),
    (
        "rejected wins over everything",
        {"total": None, "items": (), "merchant": None, "date": None},
        "rejected",
    ),
]


@pytest.mark.parametrize(
    ("overrides", "expected"), [case[1:] for case in STATUSES], ids=[case[0] for case in STATUSES]
)
def test_review_status(overrides: dict, expected: str) -> None:
    receipt = facts(**overrides)

    assert review_status(receipt, review_flags(receipt)) == expected
    assert assess(receipt, TODAY)[0] == expected


def test_review_status_uses_the_flags_it_is_given() -> None:
    """F06 and F07 may add flags (e.g. `possible_duplicate`): any flag means review."""
    [flag] = review_flags(facts(merchant=None))

    assert review_status(facts(), [flag]) == "needs_review"
    assert review_status(facts(), []) == "accepted"


def test_review_status_matches_the_api_literal() -> None:
    assert get_args(confidence.ReviewStatus) == get_args(schemas.ReviewStatus)


def test_expense_has_no_score_and_a_string_review_status() -> None:
    """Decision 0008: no score, confidence or probability field; the status is a string."""
    scored = [
        name
        for name in schemas.Expense.model_fields
        if re.search(r"score|confidence|probability", name, flags=re.IGNORECASE)
    ]
    assert scored == []

    review_status_schema = schemas.Expense.model_json_schema()["properties"]["review_status"]
    assert review_status_schema["type"] == "string"
    assert review_status_schema["enum"] == list(get_args(confidence.ReviewStatus))


# --- is_plausible_receipt -----------------------------------------------------------

PLAUSIBILITY = [
    ("complete", {}, True),
    ("one item", {"merchant": None, "total": None, "items": (item(),)}, False),
    ("no merchant, no total, no items", {"merchant": None, "total": None, "items": ()}, False),
    ("blank merchant counts as missing", {"merchant": " \n", "total": None, "items": ()}, False),
    ("two items", {"merchant": None, "total": None, "items": (item(), item())}, True),
    ("a merchant", {"total": None, "items": (item(),)}, True),
    ("a total", {"merchant": None, "items": (item(),)}, True),
    ("a zero total is a total", {"merchant": None, "total": Decimal("0"), "items": ()}, True),
    (
        "date and currency don't count",
        {"merchant": None, "total": None, "items": (), "date": "2026-10-01", "currency": "EUR"},
        False,
    ),
]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [case[1:] for case in PLAUSIBILITY],
    ids=[case[0] for case in PLAUSIBILITY],
)
def test_is_plausible_receipt(overrides: dict, expected: bool) -> None:
    assert is_plausible_receipt(facts(**overrides)) is expected


# --- assess -------------------------------------------------------------------------


def test_assess_puts_validation_flags_first() -> None:
    receipt = facts(merchant=None, total=Decimal("9.00"), date="2030-01-01", currency="$")

    status, flags = assess(receipt, TODAY)

    assert status == "needs_review"
    assert [flag.code for flag in flags] == [
        "sum_mismatch",
        "date_in_future",
        "currency_unknown",
        "missing_merchant",
    ]


def test_an_edit_clears_the_fixed_flags() -> None:
    """F06 reruns `assess` after an edit and drops an edited key from `unreadable_fields`."""
    extracted = facts(total=Decimal("4.00"), unreadable_fields=("total", "tax"))

    status, flags = assess(extracted, TODAY)
    assert status == "needs_review"
    assert pairs(flags) == [
        ("total", "sum_mismatch"),
        ("total", "unreadable"),
        ("tax", "unreadable"),
    ]

    edited = replace(extracted, total=Decimal("3.00"), unreadable_fields=("tax",))
    status, flags = assess(edited, TODAY)
    assert status == "needs_review"
    assert pairs(flags) == [("tax", "unreadable")]

    fully_edited = replace(edited, unreadable_fields=())
    assert assess(fully_edited, TODAY) == ("accepted", [])


def test_categorising_an_item_clears_its_flag() -> None:
    extracted = facts(items=(item("MILCH", "1.00", None), item("BROT", "2.00", None)))
    assert pairs(assess(extracted, TODAY)[1]) == [
        ("line_items[0]", "uncategorized_item"),
        ("line_items[1]", "uncategorized_item"),
    ]

    edited = replace(extracted, items=(item("MILCH", "1.00", "drinks"), extracted.items[1]))
    assert pairs(assess(edited, TODAY)[1]) == [("line_items[1]", "uncategorized_item")]


# --- recorded fixtures --------------------------------------------------------------


def _money(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def fixture_facts(name: str) -> ReceiptFacts:
    """The last recorded answer of a case as ReceiptFacts, items not yet categorised.

    Test-only: F05 builds the real mapping in services/. Like the pipeline, it reads no
    subtotal or tax from the model (decision 0019), though the recordings contain them.
    """
    case = load_case(name)
    content = case["responses"][-1]["body"]["choices"][0]["message"]["content"]
    extraction = ReceiptExtraction.model_validate(json.loads(content))
    return ReceiptFacts(
        merchant=extraction.merchant,
        date=extraction.date,
        currency=extraction.currency,
        subtotal=None,
        tax=None,
        total=_money(extraction.total),
        items=tuple(
            ItemFacts(line.description, Decimal(str(line.amount)), None)
            for line in extraction.line_items
        ),
        unreadable_fields=tuple(extraction.unreadable_fields),
    )


def test_valid_receipt_fixture() -> None:
    """Dated 2026-09-17 in EUR (`€`): no date or currency flag.

    The model put the payment lines (ZU ZAHLEN 15.17, BAR 20.00, ZURÜCK 4.83) into
    `line_items`, and even the 8 real items sum to 14.89, not the total. So the
    recording is flagged `sum_mismatch` on `total` (15.15), which is the rule working as
    intended. Its recorded subtotal (14.16) is no longer read (decision 0019).
    """
    receipt = fixture_facts("valid_receipt")

    status, flags = assess(receipt, TODAY)

    assert is_plausible_receipt(receipt)
    assert status == "needs_review"
    validation = [flag for flag in flags if flag.code in VALIDATION_CODES]
    assert pairs(validation) == [("total", "sum_mismatch")]
    assert validation[0].message == "Items sum to 54.89 but total is 15.15."
    unreadable = [flag.field for flag in flags if flag.code == "unreadable"]
    assert unreadable == ["merchant", "date", "total", "currency"]


def test_missing_fields_fixture() -> None:
    """No merchant, date or total, but two items: plausible by the rule, and in review.

    The issue expected this case to be implausible; with two line items it isn't
    (the rule needs at most one item).
    """
    receipt = fixture_facts("missing_fields")

    status, flags = assess(receipt, TODAY)

    assert is_plausible_receipt(receipt)
    assert status == "needs_review"
    assert pairs(flags) == [
        ("merchant", "missing_merchant"),
        ("date", "missing_date"),
        ("total", "missing_total"),
        ("line_items[0]", "uncategorized_item"),
        ("line_items[1]", "uncategorized_item"),
    ]


def test_missing_fields_fixture_with_one_item_is_implausible() -> None:
    """The base-schema pinboard shape the rule was built for: drop the second item."""
    receipt = fixture_facts("missing_fields")

    assert not is_plausible_receipt(replace(receipt, items=receipt.items[:1]))


def test_non_receipt_claimed_receipt_fixture() -> None:
    """The pinboard with an invented shop: plausible, but flagged and in review.

    The items sum to 22.98, not the invented total (25.00), so `sum_mismatch` is set on
    `total`. `date_too_old` and the `unreadable` flag catch it as well. The recorded
    `tax` in `unreadable_fields` is dropped on parsing (decision 0019).
    """
    receipt = fixture_facts("non_receipt_claimed_receipt")

    status, flags = assess(receipt, TODAY)

    assert is_plausible_receipt(receipt)
    assert status == "needs_review"
    validation = [flag for flag in flags if flag.code in VALIDATION_CODES]
    assert pairs(validation) == [("total", "sum_mismatch"), ("date", "date_too_old")]
    assert validation[0].message == "Items sum to 22.98 but total is 25.00."
    assert ("total", "unreadable") in pairs(flags)
    assert receipt.unreadable_fields == ("total",)


# --- flag codes match the wiki ------------------------------------------------------

VALIDATION_CODES = {
    "sum_mismatch",
    "date_unparseable",
    "date_in_future",
    "date_too_old",
    "currency_unknown",
}


def wiki_flag_codes() -> set[str]:
    """The codes in the first column of the `### Flag codes` table of domain-logic.md."""
    text = DOMAIN_LOGIC_MD.read_text(encoding="utf-8")
    match = re.search(r"^### Flag codes\n(.*?)(?=^#|\Z)", text, flags=re.MULTILINE | re.DOTALL)
    assert match, "domain-logic.md has no ### Flag codes section"
    rows = re.findall(r"^\|\s*`([a-z_]+)`\s*\|", match.group(1), flags=re.MULTILINE)
    assert rows, "the Flag codes section has no table rows"
    return set(rows)


def test_flag_code_literal_matches_the_wiki_table() -> None:
    assert set(get_args(FlagCode)) == wiki_flag_codes() - LATER_FEATURE_CODES


# Facts that together raise every code the rules know.
EVERY_FLAG = [
    facts(items=(item(amount="1.00", category=None),), total=Decimal("9.00")),
    facts(date="2030-01-01"),
    facts(date="2020-01-01"),
    facts(date="soon", currency="$"),
    facts(merchant=None, date=None, total=None, unreadable_fields=("tax",)),
]


def test_every_produced_code_is_in_the_wiki_table() -> None:
    produced = {flag.code for receipt in EVERY_FLAG for flag in assess(receipt, TODAY)[1]}

    assert produced == set(get_args(FlagCode))
    assert produced <= wiki_flag_codes()


# --- money must be finite -----------------------------------------------------------

NON_FINITE = ["NaN", "sNaN", "Infinity", "-Infinity"]


@pytest.mark.parametrize("value", NON_FINITE)
def test_item_amount_must_be_finite(value: str) -> None:
    with pytest.raises(ValueError, match="amount must be a finite number"):
        ItemFacts("MILCH", Decimal(value), None)


@pytest.mark.parametrize("value", NON_FINITE)
@pytest.mark.parametrize("field", ["subtotal", "tax", "total"])
def test_receipt_money_must_be_finite(field: str, value: str) -> None:
    with pytest.raises(ValueError, match=f"{field} must be a finite number"):
        facts(**{field: Decimal(value)})


def test_finite_and_missing_money_is_accepted() -> None:
    receipt = facts(subtotal=None, tax=Decimal("-0.49"), total=Decimal("0"))

    assert receipt.total == Decimal("0")

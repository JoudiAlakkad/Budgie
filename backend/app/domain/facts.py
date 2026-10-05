"""Input and output types of the F04 rules (docs/wiki/backend/domain-logic.md#input-types-f4).

The domain imports neither `ai.schema` nor `api.schemas`, so the rules take these frozen
dataclasses. F05 builds them from the model output, F06 from an edited expense.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

# Every code the F04 rules produce; the "Flag codes" table in domain-logic.md lists the
# same set (F7 adds `possible_duplicate`). A test checks the two match.
FlagCode = Literal[
    "sum_mismatch",
    "date_unparseable",
    "date_in_future",
    "date_too_old",
    "currency_unknown",
    "missing_merchant",
    "missing_date",
    "missing_total",
    "unreadable",
    "uncategorized_item",
]

UNCATEGORIZED = "uncategorized"


@dataclass(frozen=True)
class ItemFacts:
    """One line item. `category` None or "uncategorized" means not categorised yet."""

    description: str
    amount: Decimal
    category: str | None = None

    @property
    def is_categorized(self) -> bool:
        return bool(self.category and self.category.strip()) and self.category != UNCATEGORIZED


@dataclass(frozen=True)
class ReceiptFacts:
    """An extracted or edited receipt, as the rules see it."""

    merchant: str | None
    date: str | None
    currency: str | None
    subtotal: Decimal | None
    tax: Decimal | None
    total: Decimal | None
    items: tuple[ItemFacts, ...] = ()
    unreadable_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class Flag:
    """A rule result, the same shape as the `Flag` DTO: e.g. `sum_mismatch` on `total`."""

    field: str | None
    code: FlagCode
    message: str


def is_blank(text: str | None) -> bool:
    """None, empty or whitespace only: the value counts as missing."""
    return text is None or not text.strip()

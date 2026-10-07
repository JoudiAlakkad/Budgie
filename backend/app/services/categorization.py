"""The `ItemCategorizer` seam (decisions 0013, 0020; ai-extraction.md, Pipeline).

Every line item goes through it for `normalized_name`, `qty`, `unit`, `category` and
`category_source`. The caller loads the lookup table and passes it in, so the
categorizer never opens a session of its own inside the caller's transaction (0020).
`LookupCategorizer` is `domain.categorize.normalize` plus an exact-match `lookup`.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.domain.categorize import CategoryTable, lookup, normalize


@dataclass(frozen=True)
class CategorizedItem:
    normalized_name: str
    qty: Decimal | None  # what the normaliser read from the name; the model's qty wins
    unit: str | None  # belongs to `qty`: dropped when another qty is used
    category: str
    category_source: str  # seed | user | none


class ItemCategorizer(Protocol):
    def categorize(self, description: str, table: CategoryTable) -> CategorizedItem: ...


class LookupCategorizer:
    """Normalise the name, then look it up in `table` (decision 0013)."""

    def categorize(self, description: str, table: CategoryTable) -> CategorizedItem:
        normalized = normalize(description)
        category, source = lookup(normalized.name, table)
        return CategorizedItem(
            normalized_name=normalized.name,
            qty=normalized.qty,
            unit=normalized.unit,
            category=category,
            category_source=source,
        )


def paired_unit(qty: Decimal | None, item: CategorizedItem) -> str | None:
    """The normaliser's unit, only while `qty` is the normaliser's own quantity.

    `takimeki 90g` with the model's qty 1 would otherwise read "1 g".
    """
    if item.unit is None or qty is None or item.qty is None or qty != item.qty:
        return None
    return item.unit

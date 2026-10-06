"""The `ItemCategorizer` seam (decision 0013; ai-extraction.md, Pipeline).

Every line item goes through it for `normalized_name`, `qty`, `unit`, `category` and
`category_source`. Until F07, `PlaceholderCategorizer` leaves every item
`uncategorized`; F07 replaces only the provider in `services/dependencies.py`.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class CategorizedItem:
    normalized_name: str
    qty: Decimal | None  # what the normaliser read from the name; the model's qty wins
    unit: str | None
    category: str
    category_source: str  # seed | user | none


class ItemCategorizer(Protocol):
    def categorize(self, description: str) -> CategorizedItem: ...


class PlaceholderCategorizer:
    """Lowercases and collapses whitespace; categorises nothing (F07 builds the lookup)."""

    def categorize(self, description: str) -> CategorizedItem:
        return CategorizedItem(
            normalized_name=" ".join(description.lower().split()),
            qty=None,
            unit=None,
            category="uncategorized",
            category_source="none",
        )

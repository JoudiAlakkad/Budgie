"""The `ItemCategorizer` seam (decision 0013; ai-extraction.md, Pipeline).

Every line item goes through it for `normalized_name`, `qty`, `unit`, `category` and
`category_source`. Until F07, `PlaceholderCategorizer` puts names containing `pfand` or
`leergut` (umlauts folded, so `Pfandrückgabe` matches too) under `deposit` with source
`seed`, a Pfand charged and a Pfand returned alike (decision 0019), and leaves every
other item `uncategorized`; F07 replaces only the provider in `services/dependencies.py`.
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


DEPOSIT_MARKERS = ("pfand", "leergut")
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def is_deposit(normalized_name: str) -> bool:
    """True for a Pfand or Leergut line; matches on the umlaut-folded name."""
    folded = normalized_name.translate(_UMLAUTS)
    return any(marker in folded for marker in DEPOSIT_MARKERS)


class PlaceholderCategorizer:
    """Lowercases and collapses whitespace; categorises only deposits (F07 builds the lookup)."""

    def categorize(self, description: str) -> CategorizedItem:
        name = " ".join(description.lower().split())
        deposit = is_deposit(name)
        return CategorizedItem(
            normalized_name=name,
            qty=None,
            unit=None,
            category="deposit" if deposit else "uncategorized",
            category_source="seed" if deposit else "none",
        )

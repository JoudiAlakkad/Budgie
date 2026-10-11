"""The category list (domain-logic.md#categories), the one copy in the backend outside the API.

`api/` doesn't import the domain, so `app.api.schemas` keeps its own Literals; tests tie
both to the wiki and to each other.
"""

CATEGORIES: tuple[str, ...] = (
    "groceries",
    "snacks_sweets",
    "drinks",
    "tobacco",
    "household",
    "personal_care",
    "health",
    "eating_out",
    "transport",
    "clothing",
    "electronics",
    "pet",
    "other",
    "deposit",
    "discount",
)
"""Every item category, in the wiki's order: the spending ones, then the special ones."""

SPECIAL_CATEGORIES = frozenset({"deposit", "discount"})
"""Categories that are stored on items but never counted as spending."""

SPENDING_CATEGORIES = frozenset(CATEGORIES) - SPECIAL_CATEGORIES
"""The categories that count as spending (`api.schemas.SpendingCategory`)."""

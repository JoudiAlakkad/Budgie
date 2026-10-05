"""The F05 `PlaceholderCategorizer` behind the `ItemCategorizer` seam (decision 0013)."""

import pytest

from app.services.categorization import CategorizedItem, ItemCategorizer, PlaceholderCategorizer
from app.services.dependencies import get_item_categorizer


@pytest.mark.parametrize(
    ("description", "normalized"),
    [
        ("BIO BANANE 1 KG", "bio banane 1 kg"),
        ("  VOLLMILCH\t3,5%  ", "vollmilch 3,5%"),
        ("ÄPFEL  PINK\nLADY", "äpfel pink lady"),
        ("", ""),
    ],
)
def test_placeholder_normalises_whitespace_and_case_only(description: str, normalized: str) -> None:
    assert PlaceholderCategorizer().categorize(description) == CategorizedItem(
        normalized_name=normalized,
        qty=None,
        unit=None,
        category="uncategorized",
        category_source="none",
    )


def test_the_provider_is_the_placeholder_until_f07() -> None:
    categorizer: ItemCategorizer = get_item_categorizer()

    assert isinstance(categorizer, PlaceholderCategorizer)

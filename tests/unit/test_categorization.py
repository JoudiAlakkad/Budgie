"""The `PlaceholderCategorizer` behind the `ItemCategorizer` seam (decisions 0013, 0019)."""

import pytest

from app.services.categorization import (
    CategorizedItem,
    ItemCategorizer,
    PlaceholderCategorizer,
    is_deposit,
)
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


@pytest.mark.parametrize(
    ("description", "normalized"),
    [
        ("PFAND 0,25", "pfand 0,25"),
        ("Einwegpfand", "einwegpfand"),
        ("LEERGUT", "leergut"),
        ("Pfandrückgabe", "pfandrückgabe"),
        ("PFANDRÜCKGABE", "pfandrückgabe"),
        ("Pfandbon", "pfandbon"),
        ("Mehrweg  PFAND", "mehrweg pfand"),
        ("Leergut-Bon", "leergut-bon"),
    ],
)
def test_placeholder_categorises_pfand_and_leergut_as_deposit(
    description: str, normalized: str
) -> None:
    """Decision 0019: a Pfand charged and a Pfand returned are both `deposit`, not spending."""
    assert PlaceholderCategorizer().categorize(description) == CategorizedItem(
        normalized_name=normalized,
        qty=None,
        unit=None,
        category="deposit",
        category_source="seed",
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("pfand", True),
        ("leergut", True),
        ("pfandrückgabe", True),
        ("pfandrueckgabe", True),
        ("einwegpfand", True),
        ("apfel", False),
        ("pfannkuchen", False),
        ("leer", False),
        ("", False),
    ],
)
def test_is_deposit(name: str, expected: bool) -> None:
    assert is_deposit(name) is expected


@pytest.mark.parametrize("description", ["Apfel", "ÄPFEL", "Pfanne", "Gutschein", "Mehrweg"])
def test_other_names_stay_uncategorized(description: str) -> None:
    item = PlaceholderCategorizer().categorize(description)

    assert (item.category, item.category_source) == ("uncategorized", "none")


def test_the_provider_is_the_placeholder_until_f07() -> None:
    categorizer: ItemCategorizer = get_item_categorizer()

    assert isinstance(categorizer, PlaceholderCategorizer)

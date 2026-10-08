"""`domain/categories.py` and `domain/money.py`: the single category list and cents helper."""

from decimal import Decimal, InvalidOperation, localcontext
from typing import get_args

import pytest

from app.api.schemas import Category, SpendingCategory
from app.domain import budget, categories
from app.domain.money import CENT, cents
from app.services import item_categories, views


def test_the_domain_list_equals_the_api_literals_in_order() -> None:
    assert get_args(Category) == categories.CATEGORIES
    assert set(get_args(SpendingCategory)) == categories.SPENDING_CATEGORIES
    assert {"deposit", "discount"} == categories.SPECIAL_CATEGORIES
    assert len(set(categories.CATEGORIES)) == len(categories.CATEGORIES)


def test_every_user_of_the_list_shares_it() -> None:
    assert budget.SPENDING_CATEGORIES is categories.SPENDING_CATEGORIES
    assert frozenset(categories.CATEGORIES) == item_categories.KNOWN_CATEGORIES


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.005", "1.01"),  # half up, not half even
        ("1.015", "1.02"),
        ("-1.005", "-1.01"),  # away from zero
        ("2.994", "2.99"),
        ("3", "3.00"),
    ],
)
def test_cents_rounds_half_up(value: str, expected: str) -> None:
    assert cents(Decimal(value)) == Decimal(expected)
    assert Decimal("0.01") == CENT


def test_cents_works_in_the_callers_context() -> None:
    """The sum check widens the context for float-born amounts like 1e30 (validation.py)."""
    huge = Decimal(1e30)

    with pytest.raises(InvalidOperation):
        cents(huge)  # 33 digits don't fit the default 28
    with localcontext(prec=400):
        assert cents(huge) == huge.quantize(CENT)


def test_the_services_round_with_the_same_helper() -> None:
    assert views.cents is cents

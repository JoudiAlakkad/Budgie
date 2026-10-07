"""The item lookup table: seed loading and sync, and the `/item-categories` use cases
(decisions 0013, 0020; contracts/api-endpoints.md#item-categories-the-lookup-table)."""

import functools
import logging
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from app.db.records import ItemCategoryRecord
from app.db.repositories.item_categories import SEED, ItemCategoryRepository
from app.db.session import Database
from app.errors import InvalidState, NotFound
from app.services.views import ItemCategoryView

logger = logging.getLogger(__name__)

SEED_FILE = "data/item_categories_seed.yaml"

# Mirrors `app.api.schemas.Category` (services don't import api); a test checks they match.
KNOWN_CATEGORIES = frozenset(
    {
        "groceries.fresh",
        "groceries.staples",
        "snacks_sweets",
        "drinks",
        "alcohol",
        "tobacco",
        "household",
        "personal_care",
        "health",
        "eating_out",
        "transport",
        "clothing",
        "electronics",
        "other",
        "deposit",
        "discount",
    }
)


class InvalidSeed(ValueError):
    """The seed file is not `{category: [name, ...]}` with known categories, unique names."""


def parse_seed(data: Any) -> dict[str, str]:
    """`{category: [name, ...]}` as `{name: category}`; raises `InvalidSeed`.

    Names aren't checked for being normalised here (a test does that); the lookup would
    just never hit a name that isn't.
    """
    if not isinstance(data, dict):
        raise InvalidSeed("the seed must map categories to lists of names")
    seed: dict[str, str] = {}
    for category, names in data.items():
        if category not in KNOWN_CATEGORIES:
            raise InvalidSeed(f"unknown category {category!r}")
        if not isinstance(names, list):
            raise InvalidSeed(f"{category}: expected a list of names")
        for name in names:
            if not isinstance(name, str) or not name.strip():
                raise InvalidSeed(f"{category}: every name must be a non-empty string")
            if name in seed:
                raise InvalidSeed(f"{name!r} is listed twice")
            seed[name] = category
    return seed


def read_seed(path: Path) -> dict[str, str]:
    """Load and check a seed file (`yaml.safe_load` only); raises `OSError`, `YAMLError`
    or `InvalidSeed`."""
    with path.open(encoding="utf-8") as handle:
        return parse_seed(yaml.safe_load(handle))


@functools.cache
def load_seed() -> Mapping[str, str]:
    """The seed shipped in the package (`app/domain/data/`), read once per process."""
    with resources.as_file(resources.files("app.domain").joinpath(SEED_FILE)) as path:
        return read_seed(path)


def sync_seed(db: Database, seed: Mapping[str, str] | None = None) -> None:
    """Startup: insert missing seed names and update rows still `seed`; `user` rows stay.

    Raises whatever loading or storing raises; `prepare_storage` logs it and goes on.
    """
    values = load_seed() if seed is None else seed
    with db.transaction() as session:
        result = ItemCategoryRepository(session).sync_seed(values)
    logger.info(
        "Item category seed synced: %d names, %d inserted, %d updated",
        len(values),
        result.inserted,
        result.updated,
    )


def item_category_view(record: ItemCategoryRecord) -> ItemCategoryView:
    return ItemCategoryView(
        normalized_name=record.normalized_name,
        category=record.category,
        source=record.source,
        updated_at=record.updated_at,
    )


class ItemCategoryService:
    """`GET`, `PUT` and `DELETE /item-categories`. The path name is used as given, never
    normalised again (contract)."""

    def __init__(self, db: Database, seed: Mapping[str, str] | None = None) -> None:
        self._db = db
        self._seed = seed

    def _seed_values(self) -> Mapping[str, str]:
        return load_seed() if self._seed is None else self._seed

    def search(self, q: str | None = None) -> list[ItemCategoryView]:
        """Entries whose name contains `q`, sorted by name."""
        with self._db.transaction() as session:
            records = ItemCategoryRepository(session).search(q)
        return [item_category_view(record) for record in records]

    def put(self, name: str, category: str) -> ItemCategoryView:
        """Set the category with source `user`; overrides a seed entry."""
        with self._db.transaction() as session:
            record = ItemCategoryRepository(session).upsert_user(name, category)
        logger.info("Item category set by the user")
        return item_category_view(record)

    def delete(self, name: str) -> None:
        """Remove a `user` entry; the seed value comes back if the seed has the name.

        `NotFound` for an unknown name, `InvalidState` for a seed entry.
        """
        seed_category = self._seed_values().get(name)
        with self._db.transaction() as session:
            repo = ItemCategoryRepository(session)
            current = repo.get(name)
            if current is None:
                raise NotFound(f'No item category for "{name}".')
            if current.source == SEED:
                raise InvalidState(
                    f'"{name}" is a seed entry; only a user entry can be removed. '
                    "Set another category instead."
                )
            repo.delete(name)
            if seed_category is not None:
                repo.put_seed(name, seed_category)
        logger.info(
            "Item category removed by the user, seed restored: %s", seed_category is not None
        )

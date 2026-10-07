"""The item lookup table `item_categories` (decisions 0013, 0020; persistence.md)."""

import datetime as dt
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ItemCategoryRow
from app.db.records import ItemCategoryRecord, SeedSync

SEED = "seed"
USER = "user"


def _now() -> dt.datetime:
    """Naive UTC, as stored (persistence.md)."""
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def to_record(row: ItemCategoryRow) -> ItemCategoryRecord:
    return ItemCategoryRecord(
        normalized_name=row.normalized_name,
        category=row.category,
        source=row.source,
        updated_at=row.updated_at,
    )


class ItemCategoryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def table(self) -> dict[str, tuple[str, str]]:
        """The whole table as normalized name -> (category, source), for the lookup."""
        rows = self._session.execute(
            select(
                ItemCategoryRow.normalized_name, ItemCategoryRow.category, ItemCategoryRow.source
            )
        )
        return {name: (category, source) for name, category, source in rows}

    def search(self, q: str | None = None) -> list[ItemCategoryRecord]:
        """Every entry whose name contains `q` (all without one), sorted by name.

        `%` and `_` in `q` match themselves.
        """
        query = select(ItemCategoryRow).order_by(ItemCategoryRow.normalized_name)
        if q:
            query = query.where(ItemCategoryRow.normalized_name.contains(q, autoescape=True))
        return [to_record(row) for row in self._session.scalars(query)]

    def get(self, name: str) -> ItemCategoryRecord | None:
        row = self._session.get(ItemCategoryRow, name)
        return to_record(row) if row is not None else None

    def upsert_user(self, name: str, category: str) -> ItemCategoryRecord:
        """A user's choice: insert, or overwrite whatever the row held (a seed entry too)."""
        return self._put(name, category, USER)

    def put_seed(self, name: str, category: str) -> ItemCategoryRecord:
        """Insert or overwrite the row as a seed entry (restoring the seed after a delete)."""
        return self._put(name, category, SEED)

    def _put(self, name: str, category: str, source: str) -> ItemCategoryRecord:
        row = self._session.get(ItemCategoryRow, name)
        if row is None:
            row = ItemCategoryRow(normalized_name=name)
            self._session.add(row)
        if row.category != category or row.source != source or row.updated_at is None:
            row.category = category
            row.source = source
            row.updated_at = _now()
        self._session.flush()
        return to_record(row)

    def delete(self, name: str) -> bool:
        """Remove the row; True if it existed."""
        row = self._session.get(ItemCategoryRow, name)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True

    def sync_seed(self, seed: Mapping[str, str]) -> SeedSync:
        """Insert the seed names that are missing and give rows still `seed` the seed's
        value; `user` rows and names not in the seed are left alone (decision 0020)."""
        # The whole table, not `IN (<every seed name>)`: no bound-parameter limit to hit.
        rows = {row.normalized_name: row for row in self._session.scalars(select(ItemCategoryRow))}
        inserted = updated = 0
        now = _now()
        for name, category in seed.items():
            row = rows.get(name)
            if row is None:
                self._session.add(
                    ItemCategoryRow(
                        normalized_name=name, category=category, source=SEED, updated_at=now
                    )
                )
                inserted += 1
            elif row.source == SEED and row.category != category:
                row.category = category
                row.updated_at = now
                updated += 1
        self._session.flush()
        return SeedSync(inserted=inserted, updated=updated)

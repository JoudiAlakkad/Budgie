"""`ItemCategoryService.delete` restores a seed value by updating the row (review, F07)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import event

from app.db.repositories.item_categories import ItemCategoryRepository
from app.db.session import Database
from app.services.item_categories import ItemCategoryService

SEED = {"banane": "groceries.fresh"}


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database(f"sqlite:///{tmp_path / 'budgie.db'}")
    database.init_db()
    yield database
    database.dispose()


def statements_during(db: Database, action) -> list[str]:  # type: ignore[no-untyped-def]
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany) -> None:  # type: ignore[no-untyped-def]
        seen.append(statement.lstrip().upper())

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        action()
    finally:
        event.remove(db.engine, "before_cursor_execute", record)
    return seen


def table(db: Database) -> dict[str, tuple[str, str]]:
    with db.transaction() as session:
        return ItemCategoryRepository(session).table()


def test_a_user_row_with_a_seed_value_is_updated_not_deleted(db: Database) -> None:
    service = ItemCategoryService(db, seed=SEED)
    service.put("banane", "snacks_sweets")

    seen = statements_during(db, lambda: service.delete("banane"))

    assert not [s for s in seen if s.startswith("DELETE")]
    assert table(db) == {"banane": ("groceries.fresh", "seed")}


def test_a_user_row_without_a_seed_value_is_deleted(db: Database) -> None:
    service = ItemCategoryService(db, seed=SEED)
    service.put("zwiebelkuchen", "eating_out")

    seen = statements_during(db, lambda: service.delete("zwiebelkuchen"))

    assert [s for s in seen if s.startswith("DELETE")]
    assert table(db) == {}

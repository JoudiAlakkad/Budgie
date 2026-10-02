"""`Database` builds its engine lazily and maps every bad DATABASE_URL to StorageError."""

from pathlib import Path

import pytest

from app.db.session import Database
from app.errors import StorageError

BAD_URLS = [
    pytest.param("not a url", id="malformed"),
    pytest.param("", id="empty"),
    pytest.param("sqlite://host:abc/x", id="bad-port"),
    pytest.param("nosuchdialect://a/b", id="unknown-dialect"),
    pytest.param("mysql://budgie@db-host/budgie", id="driver-missing"),
]


@pytest.mark.parametrize("url", BAD_URLS)
def test_bad_url_constructs_then_raises_storage_error(url: str) -> None:
    db = Database(url)  # must not raise: startup builds this before any error handling

    for use in (db.init_db, db.ping, lambda: db.session_factory):
        with pytest.raises(StorageError):
            use()
    db.dispose()  # no engine was built; nothing to dispose, no error


def test_good_url_initialises_pings_and_reuses_engine(tmp_path: Path) -> None:
    db = Database(f"sqlite:///{tmp_path / 'nested' / 'budgie.db'}")

    db.init_db()
    db.ping()

    assert (tmp_path / "nested" / "budgie.db").is_file()
    assert db.engine is db.engine
    assert db.session_factory is db.session_factory
    db.dispose()


def test_ping_on_unreachable_database_raises_storage_error(tmp_path: Path) -> None:
    db = Database(f"sqlite:///{tmp_path / 'missing-dir' / 'budgie.db'}")  # no init_db

    with pytest.raises(StorageError):
        db.ping()
    db.dispose()


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:"])
def test_in_memory_sqlite_needs_no_directory(url: str) -> None:
    db = Database(url)

    db.init_db()
    db.ping()
    db.dispose()

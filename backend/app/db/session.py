"""Engine and session factory for DATABASE_URL."""

from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base
from app.errors import StorageError


def _sqlite_file(url: str) -> Path | None:
    """Return the database file of a file-based SQLite URL, else None."""
    parsed = make_url(url)
    if not parsed.drivername.startswith("sqlite"):
        return None
    database = parsed.database
    if not database or database == ":memory:" or database.startswith("file:"):
        return None
    return Path(database)


class Database:
    """Owns the engine and session factory. Other layers treat it as opaque."""

    def __init__(self, url: str) -> None:
        self.url = url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, connect_args=connect_args)
        self.session_factory: sessionmaker[Session] = sessionmaker(
            bind=self.engine, expire_on_commit=False
        )

    def init_db(self) -> None:
        """Create the SQLite parent directory and all tables."""
        try:
            db_file = _sqlite_file(self.url)
            if db_file is not None:
                db_file.parent.mkdir(parents=True, exist_ok=True)
            Base.metadata.create_all(self.engine)
        except (SQLAlchemyError, OSError) as exc:
            raise StorageError("The database could not be initialised.") from exc

    def ping(self) -> None:
        """Run SELECT 1; raise StorageError if the database is unusable."""
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError) as exc:
            raise StorageError("The database is not reachable.") from exc

    def dispose(self) -> None:
        """Close pooled connections."""
        self.engine.dispose()

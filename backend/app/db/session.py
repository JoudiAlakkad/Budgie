"""Engine and session factory for DATABASE_URL."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base
from app.errors import StorageError

# What a bad DATABASE_URL raises while the engine is built: ArgumentError (unparsable URL,
# unknown dialect), ImportError (driver not installed, e.g. postgresql://), ValueError (bad port).
_ENGINE_ERRORS = (SQLAlchemyError, ImportError, ValueError)


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
    """Owns the engine and session factory. Other layers treat it as opaque.

    The engine is built on first use, so a bad DATABASE_URL surfaces as StorageError from
    `init_db`, `ping` or `session_factory` instead of crashing app startup. A failed build is
    not cached; the next call tries again.
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None
        self._lock = Lock()

    @property
    def engine(self) -> Engine:
        """The engine for `url`; raises StorageError if it cannot be built."""
        with self._lock:
            if self._engine is None:
                connect_args = {"check_same_thread": False} if self.url.startswith("sqlite") else {}
                try:
                    # hide_parameters: a logged traceback (api/errors.py logs StorageError
                    # with its cause) must never quote a merchant or a description.
                    self._engine = create_engine(
                        self.url, connect_args=connect_args, hide_parameters=True
                    )
                except _ENGINE_ERRORS as exc:
                    raise StorageError("DATABASE_URL is not usable.") from exc
            return self._engine

    @property
    def session_factory(self) -> sessionmaker[Session]:
        """Session factory bound to the engine; raises StorageError like `engine`."""
        engine = self.engine
        with self._lock:
            if self._session_factory is None:
                self._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
            return self._session_factory

    def init_db(self) -> None:
        """Create the SQLite parent directory and all tables."""
        engine = self.engine
        try:
            db_file = _sqlite_file(self.url)
            if db_file is not None:
                db_file.parent.mkdir(parents=True, exist_ok=True)
            Base.metadata.create_all(engine)
        except (SQLAlchemyError, OSError) as exc:
            raise StorageError("The database could not be initialised.") from exc

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """A session in one transaction: committed on exit, rolled back on an error.

        `SQLAlchemyError` and `OSError` become `StorageError`; any other exception (e.g. a
        `NotFound` raised by the caller inside the block) rolls back and passes unchanged.
        """
        factory = self.session_factory
        try:
            with factory.begin() as session:
                yield session
        except (SQLAlchemyError, OSError) as exc:
            raise StorageError() from exc

    def ping(self) -> None:
        """Run SELECT 1; raise StorageError if the database is unusable."""
        engine = self.engine
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError) as exc:
            raise StorageError("The database is not reachable.") from exc

    def dispose(self) -> None:
        """Close pooled connections, if an engine was ever built."""
        with self._lock:
            if self._engine is not None:
                self._engine.dispose()

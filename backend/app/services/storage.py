"""Startup and shutdown of the service's private storage (decision 0006)."""

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fastapi import Request

from app.config import Settings
from app.errors import StorageError
from app.services.dependencies import database_for, dispose_databases

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StorageStatus:
    """Outcome of `prepare_storage`. Health reports `db: error` unless it is ok."""

    prepared: bool
    failures: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.prepared and not self.failures


# Before the lifespan has run, nothing has been created yet.
NOT_PREPARED = StorageStatus(prepared=False)


def _prepare_upload_dir(upload_dir: str) -> None:
    """Create UPLOAD_DIR and prove it is writable."""
    try:
        path = Path(upload_dir)
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=path):
            pass
    except OSError as exc:
        raise StorageError("The upload directory is not usable.") from exc


def prepare_storage(settings: Settings) -> StorageStatus:
    """Create the upload directory and the database tables.

    Both steps always run, so a broken upload dir does not skip table creation. Failures
    are logged and returned, never raised: the app keeps serving so /api/health can report
    `db: error` (persistence.md).
    """
    failures: list[str] = []
    steps = (
        ("upload_dir", lambda: _prepare_upload_dir(settings.upload_dir)),
        ("database", lambda: database_for(settings.database_url).init_db()),
    )
    for name, step in steps:
        try:
            step()
        except StorageError:
            logger.exception("Storage step %r failed at startup; continuing without it", name)
            failures.append(name)
    return StorageStatus(prepared=True, failures=tuple(failures))


def get_storage_status(request: Request) -> StorageStatus:
    """FastAPI dependency: the startup storage status kept on the app state."""
    return getattr(request.app.state, "storage_status", NOT_PREPARED)


def close_storage() -> None:
    dispose_databases()

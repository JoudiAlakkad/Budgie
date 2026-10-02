"""Startup and shutdown of the service's private storage (decision 0006)."""

from pathlib import Path

from app.config import Settings
from app.errors import StorageError
from app.services.dependencies import database_for, dispose_databases


def prepare_storage(settings: Settings) -> None:
    """Create the upload directory and the database tables."""
    try:
        Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StorageError("The upload directory could not be created.") from exc
    database_for(settings.database_url).init_db()


def close_storage() -> None:
    dispose_databases()

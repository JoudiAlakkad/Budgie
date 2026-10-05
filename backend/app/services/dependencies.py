"""FastAPI dependency providers for infrastructure.

Routers depend on these (through services), never on `db` or `ai` directly.
Tests swap them out with `app.dependency_overrides`.
"""

from collections.abc import Callable
from threading import Lock

from fastapi import Depends

from app.ai.client import LLMClient
from app.ai.extractor import Extractor
from app.ai.prompts import load_prompts
from app.config import Settings, get_settings
from app.db.images import ImageStore
from app.db.session import Database
from app.services.receipts import ReceiptService

_databases: dict[str, Database] = {}
_databases_lock = Lock()


def database_for(url: str) -> Database:
    """One Database (engine + pool) per URL for the life of the process."""
    with _databases_lock:
        db = _databases.get(url)
        if db is None:
            db = _databases[url] = Database(url)
        return db


def dispose_databases() -> None:
    """Dispose and forget all cached engines (on shutdown and in tests)."""
    with _databases_lock:
        for db in _databases.values():
            db.dispose()
        _databases.clear()


def get_database(settings: Settings = Depends(get_settings)) -> Database:
    return database_for(settings.database_url)


def get_db_ping(db: Database = Depends(get_database)) -> Callable[[], None]:
    """A callable that runs SELECT 1 and raises StorageError on failure."""
    return db.ping


def get_image_store(settings: Settings = Depends(get_settings)) -> ImageStore:
    return ImageStore(settings.upload_dir)


def get_receipt_service(
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_database),
    images: ImageStore = Depends(get_image_store),
) -> ReceiptService:
    return ReceiptService(db, images, settings.max_upload_mb)


def get_llm_client(settings: Settings = Depends(get_settings)) -> LLMClient:
    return LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        timeout=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
    )


def get_extractor(
    settings: Settings = Depends(get_settings),
    client: LLMClient = Depends(get_llm_client),
) -> Extractor:
    """The receipt extractor for `PROMPT_VERSION`; the pipeline (F05) will use it."""
    return Extractor(
        client,
        load_prompts(settings.prompt_version),
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
    )

"""FastAPI dependency providers for infrastructure and services.

Routers depend on these (through services), never on `db` or `ai` directly.
Tests swap them out with `app.dependency_overrides`. A provider must not declare request
parameters, or the OpenAPI spec would change (modules.md).
"""

import datetime as dt
from collections.abc import Callable
from threading import Lock
from zoneinfo import ZoneInfo

from fastapi import Depends

from app.ai.client import LLMClient
from app.ai.extractor import Extractor
from app.ai.prompts import load_prompts
from app.config import Settings, get_settings
from app.db.images import ImageStore
from app.db.session import Database
from app.services.budgets import BudgetService
from app.services.categorization import ItemCategorizer, LookupCategorizer
from app.services.expenses import ExpenseService
from app.services.insights import InsightsService
from app.services.item_categories import ItemCategoryService
from app.services.receipt_pipeline import ExtractorFactory, ReceiptPipeline, Today
from app.services.receipts import ReceiptService

# Budgie targets German receipts; "today" for the date rules is the local date there.
BERLIN = ZoneInfo("Europe/Berlin")

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


def build_extractor(settings: Settings, client: LLMClient) -> Extractor:
    """The receipt extractor for `PROMPT_VERSION`; raises `UnknownPromptVersion`."""
    return Extractor(
        client,
        load_prompts(settings.prompt_version),
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
    )


def get_extractor_factory(
    settings: Settings = Depends(get_settings),
    client: LLMClient = Depends(get_llm_client),
) -> ExtractorFactory:
    """Builds the extractor inside the task, so a bad PROMPT_VERSION fails the receipt."""
    return lambda: build_extractor(settings, client)


def berlin_date(instant: dt.datetime) -> dt.date:
    """The Europe/Berlin calendar date of an aware instant."""
    return instant.astimezone(BERLIN).date()


def today_in_berlin() -> dt.date:
    return berlin_date(dt.datetime.now(dt.UTC))


def get_today() -> Today:
    """A clock for the date rules, called when the rules run (inside the task for an
    extraction), not when the request arrives. Tests override it."""
    return today_in_berlin


def get_item_categorizer() -> ItemCategorizer:
    """Normaliser plus exact-match lookup in a table the caller passes (0013, 0020)."""
    return LookupCategorizer()


def get_receipt_pipeline(
    db: Database = Depends(get_database),
    images: ImageStore = Depends(get_image_store),
    extractor_factory: ExtractorFactory = Depends(get_extractor_factory),
    categorizer: ItemCategorizer = Depends(get_item_categorizer),
    today: Today = Depends(get_today),
) -> ReceiptPipeline:
    return ReceiptPipeline(db, images, extractor_factory, categorizer, today)


def get_expense_service(
    db: Database = Depends(get_database),
    categorizer: ItemCategorizer = Depends(get_item_categorizer),
    today: Today = Depends(get_today),
    images: ImageStore = Depends(get_image_store),
) -> ExpenseService:
    return ExpenseService(db, categorizer, today, images)


def get_item_category_service(db: Database = Depends(get_database)) -> ItemCategoryService:
    return ItemCategoryService(db)


def get_budget_service(
    db: Database = Depends(get_database), today: Today = Depends(get_today)
) -> BudgetService:
    return BudgetService(db, today)


def get_insights_service(
    db: Database = Depends(get_database), today: Today = Depends(get_today)
) -> InsightsService:
    return InsightsService(db, today)

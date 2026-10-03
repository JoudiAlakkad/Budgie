"""App factory: routers, static frontend mount, startup."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import budgets, expenses, health, insights, item_categories, receipts
from app.api.errors import error_responses, install_error_handlers
from app.config import Settings, get_settings
from app.services.storage import close_storage, prepare_storage

logger = logging.getLogger(__name__)


def _configure_logging(level_name: str) -> None:
    level = logging.getLevelNamesMapping().get(level_name.upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("app").setLevel(level)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Passing `settings` also makes every dependency use them."""
    settings = settings or get_settings()
    _configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Never raises: failures are logged and kept on the app state, where
        # /api/health reads them to report `db: error` (persistence.md).
        app.state.storage_status = prepare_storage(settings)
        logger.info("Budgie started (model %s)", settings.llm_model)
        yield
        close_storage()

    # One schema per model for requests and responses, so DTOs like `Budget` and `Goal`
    # appear once in the spec (decision 0016).
    app = FastAPI(
        title="Budgie",
        version=__version__,
        lifespan=lifespan,
        separate_input_output_schemas=False,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    install_error_handlers(app)

    app.include_router(health.router, prefix="/api")
    # Every operation documents the shared error body for 422 and 500; this also replaces
    # FastAPI's default `HTTPValidationError`. 501 stays undocumented per route (0016).
    for module in (receipts, expenses, item_categories, budgets, insights):
        app.include_router(module.router, prefix="/api", responses=error_responses(422, 500))

    # Mounted last so /api, /docs and /openapi.json win over the static files.
    # An empty FRONTEND_DIR means "no frontend"; Path("") would otherwise be the cwd.
    if not settings.frontend_dir:
        return app
    frontend_dir = Path(settings.frontend_dir)
    if frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
    else:
        logger.warning("FRONTEND_DIR %s not found; serving the API only", frontend_dir)

    return app


app = create_app()

"""App factory: routers, static frontend mount, startup."""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.routing import Match, Mount
from starlette.types import Scope

from app import __version__
from app.api import budgets, expenses, health, insights, item_categories, receipts
from app.api.errors import error_responses, install_error_handlers
from app.config import Settings, get_settings
from app.services.dependencies import database_for
from app.services.receipts import reset_interrupted
from app.services.storage import close_storage, prepare_storage

logger = logging.getLogger(__name__)

API_PREFIX = "/api"


class FrontendMount(Mount):
    """The static frontend at `/`, which never claims a path under `/api`.

    Starlette's router takes the first full match. A plain mount at `/` fully matches
    every path, so `/api/nope` would get the static 404 (or 405 for POST) and an API path
    with the wrong method would never reach the router's 405 with its `Allow` header.
    Declining `/api` paths leaves them to the API routes and their error format.
    """

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        path: str = scope.get("path", "")
        root_path: str = scope.get("root_path", "")
        if root_path and path.startswith(root_path):
            path = path[len(root_path) :]
        if path == API_PREFIX or path.startswith(API_PREFIX + "/"):
            return Match.NONE, {}
        return super().matches(scope)


class NoCacheStaticFiles(StaticFiles):
    """The frontend's files with `Cache-Control: no-cache`: the browser must revalidate
    every file before using it, and the ETag/Last-Modified answer an unchanged one with a
    cheap 304 (which keeps the header too).

    Without it, browsers guess a freshness from Last-Modified, so an old cached module
    (`js/dom.js`) could be paired with new modules that import a name it lacks; the ES
    module link then fails and the page stops working (F08, found by hand). API
    responses don't pass through here.
    """

    def file_response(
        self,
        full_path: str | os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = "no-cache"
        return response


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
        # Tasks that were queued or running died with the last process (decision 0007).
        # Only the tables are needed, so a broken upload dir doesn't skip the reset.
        if app.state.storage_status.database_ok:
            reset_interrupted(database_for(settings.database_url))
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

    app.include_router(health.router, prefix=API_PREFIX)
    # Every operation documents the shared error body for 422 and 500; this also replaces
    # FastAPI's default `HTTPValidationError`. 501 stays undocumented per route (0016).
    for module in (receipts, expenses, item_categories, budgets, insights):
        app.include_router(module.router, prefix=API_PREFIX, responses=error_responses(422, 500))

    # Mounted last so /api, /docs and /openapi.json win over the static files.
    # An empty FRONTEND_DIR means "no frontend"; Path("") would otherwise be the cwd.
    if not settings.frontend_dir:
        return app
    frontend_dir = Path(settings.frontend_dir)
    if frontend_dir.is_dir():
        app.router.routes.append(
            FrontendMount(
                "/", app=NoCacheStaticFiles(directory=frontend_dir, html=True), name="frontend"
            )
        )
    else:
        logger.warning("FRONTEND_DIR %s not found; serving the API only", frontend_dir)

    return app


app = create_app()

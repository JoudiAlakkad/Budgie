"""Health check use case: database and model server reachability."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from fastapi import Depends

from app.config import Settings, get_settings
from app.errors import StorageError
from app.services.dependencies import get_db_ping, get_llm_client


class Pingable(Protocol):
    def ping(self) -> bool: ...


@dataclass(frozen=True)
class HealthReport:
    status: Literal["ok"]
    db: Literal["ok", "error"]
    llm: Literal["ok", "down"]
    model: str


def check_health(db_ping: Callable[[], None], llm_client: Pingable, model: str) -> HealthReport:
    """Report db `ok|error` and llm `ok|down`. The service itself is always `ok`."""
    try:
        db_ping()
        db: Literal["ok", "error"] = "ok"
    except StorageError:
        db = "error"
    llm: Literal["ok", "down"] = "ok" if llm_client.ping() else "down"
    return HealthReport(status="ok", db=db, llm=llm, model=model)


def current_health(
    db_ping: Callable[[], None] = Depends(get_db_ping),
    llm_client: Pingable = Depends(get_llm_client),
    settings: Settings = Depends(get_settings),
) -> HealthReport:
    """FastAPI dependency: the health report for the running service."""
    return check_health(db_ping, llm_client, settings.llm_model)

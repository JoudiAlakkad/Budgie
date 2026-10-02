"""`check_health` maps db and model reachability to the contract values."""

import pytest

from app.errors import StorageError
from app.services.health import HealthReport, check_health
from tests.conftest import FakeLLMClient


def ok() -> None:
    return None


def broken() -> None:
    raise StorageError("down")


@pytest.mark.parametrize(
    ("db_ping", "llm_up", "expected_db", "expected_llm"),
    [
        (ok, True, "ok", "ok"),
        (ok, False, "ok", "down"),
        (broken, True, "error", "ok"),
        (broken, False, "error", "down"),
    ],
)
def test_check_health(db_ping, llm_up: bool, expected_db: str, expected_llm: str) -> None:
    report = check_health(db_ping, FakeLLMClient(up=llm_up), "gemma3:4b")

    assert report == HealthReport(status="ok", db=expected_db, llm=expected_llm, model="gemma3:4b")


def test_failed_startup_storage_is_db_error_even_if_ping_works() -> None:
    pinged: list[bool] = []

    report = check_health(lambda: pinged.append(True), FakeLLMClient(up=True), "m", False)

    assert report.db == "error"
    assert report.llm == "ok"
    assert pinged == []

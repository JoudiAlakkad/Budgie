"""`prepare_storage` runs every step and reports failures instead of raising."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.config import Settings
from app.services.dependencies import dispose_databases
from app.services.storage import NOT_PREPARED, StorageStatus, prepare_storage


@pytest.fixture(autouse=True)
def _dispose() -> Iterator[None]:
    yield
    dispose_databases()


def make_settings(tmp_path: Path, **update: str) -> Settings:
    values = {
        "database_url": f"sqlite:///{tmp_path / 'budgie.db'}",
        "upload_dir": str(tmp_path / "uploads"),
    }
    return Settings(_env_file=None, **(values | update))


def test_all_steps_ok(tmp_path: Path) -> None:
    status = prepare_storage(make_settings(tmp_path))

    assert status == StorageStatus(prepared=True)
    assert status.ok
    assert (tmp_path / "uploads").is_dir()
    assert (tmp_path / "budgie.db").is_file()


def test_upload_dir_failure_does_not_skip_database(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("", encoding="utf-8")

    status = prepare_storage(make_settings(tmp_path, upload_dir=str(tmp_path / "file")))

    assert status.failures == ("upload_dir",)
    assert not status.ok
    assert (tmp_path / "budgie.db").is_file()


def test_database_failure_does_not_skip_upload_dir(tmp_path: Path) -> None:
    status = prepare_storage(make_settings(tmp_path, database_url="not a url"))

    assert status.failures == ("database",)
    assert (tmp_path / "uploads").is_dir()


def test_both_fail(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("", encoding="utf-8")

    status = prepare_storage(
        make_settings(tmp_path, upload_dir=str(tmp_path / "file"), database_url="not a url")
    )

    assert status.failures == ("upload_dir", "database")


def test_not_prepared_is_not_ok() -> None:
    assert not NOT_PREPARED.ok


@pytest.mark.parametrize(
    ("status", "database_ok"),
    [
        (StorageStatus(prepared=True), True),
        (StorageStatus(prepared=True, failures=("upload_dir",)), True),
        (StorageStatus(prepared=True, failures=("database",)), False),
        (StorageStatus(prepared=True, failures=("upload_dir", "database")), False),
        (NOT_PREPARED, False),
    ],
)
def test_database_ok_ignores_the_upload_dir(status: StorageStatus, database_ok: bool) -> None:
    assert status.database_ok is database_ok

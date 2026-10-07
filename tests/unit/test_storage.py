"""`prepare_storage` runs every step and reports failures instead of raising; it syncs the
item category seed once the tables exist (F07, decision 0020)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from app.config import Settings
from app.db.repositories.item_categories import ItemCategoryRepository
from app.errors import StorageError
from app.services import item_categories, storage
from app.services.dependencies import database_for, dispose_databases
from app.services.item_categories import InvalidSeed, load_seed
from app.services.storage import NOT_PREPARED, SEED_STEP, StorageStatus, prepare_storage


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


# ---------------------------------------------------------------- item category seed (F07)


def lookup_table(settings: Settings) -> dict[str, tuple[str, str]]:
    with database_for(settings.database_url).transaction() as session:
        return ItemCategoryRepository(session).table()


def test_startup_syncs_the_seed(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    assert prepare_storage(settings) == StorageStatus(prepared=True)

    table = lookup_table(settings)
    assert table == {name: (category, "seed") for name, category in load_seed().items()}


def test_a_restart_keeps_user_choices_and_restores_the_seed(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    prepare_storage(settings)
    with database_for(settings.database_url).transaction() as session:
        repo = ItemCategoryRepository(session)
        repo.upsert_user("banane", "snacks_sweets")
        repo.put_seed("kaffee", "other")  # a seed row with an old value
        repo.delete("pfand")

    assert prepare_storage(settings) == StorageStatus(prepared=True)

    table = lookup_table(settings)
    assert table["banane"] == ("snacks_sweets", "user")
    assert table["kaffee"] == (load_seed()["kaffee"], "seed")
    assert table["pfand"] == ("deposit", "seed")


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("seed missing"),
        InvalidSeed("unknown category"),
        yaml.YAMLError("broken"),
    ],
    ids=["missing file", "invalid seed", "broken yaml"],
)
def test_a_seed_that_cannot_be_loaded_is_reported_but_never_stops_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    def broken() -> None:
        raise error

    monkeypatch.setattr(item_categories, "load_seed", broken)

    status = prepare_storage(make_settings(tmp_path))

    assert status.failures == (SEED_STEP,)
    assert status.database_ok  # the tables are there; the startup reset still runs
    assert not status.ok  # health reports db: error, so the docker gate catches it
    # sync_seed wraps it in StorageError; the log names the original type only.
    assert (
        f"Storage step {SEED_STEP} failed at startup ({type(error).__name__}); "
        "continuing without it" in caplog.messages
    )
    assert all(record.exc_info is None for record in caplog.records)


def test_sync_seed_wraps_load_errors_in_storage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> None:
        raise InvalidSeed("unknown category")

    monkeypatch.setattr(item_categories, "load_seed", broken)
    db = database_for(make_settings(tmp_path).database_url)

    with pytest.raises(StorageError) as caught:
        item_categories.sync_seed(db)

    assert isinstance(caught.value.__cause__, InvalidSeed)


def test_a_storage_error_while_syncing_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(db: object) -> None:
        raise StorageError()

    monkeypatch.setattr(storage, "sync_seed", broken)

    status = prepare_storage(make_settings(tmp_path))

    assert status.failures == (SEED_STEP,)


@pytest.mark.parametrize("step", ["sync_seed", "_prepare_upload_dir"])
def test_a_bug_in_a_step_is_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    """Only StorageError is caught (review): a programming error surfaces."""

    def bug(*args: object) -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr(storage, step, bug)

    with pytest.raises(RuntimeError):
        prepare_storage(make_settings(tmp_path))


def test_no_seed_sync_without_tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(storage, "sync_seed", calls.append)

    status = prepare_storage(make_settings(tmp_path, database_url="not a url"))

    assert status.failures == ("database",)
    assert calls == []

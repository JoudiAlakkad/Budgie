"""`ImageStore`: random names, atomic writes, paths kept inside UPLOAD_DIR."""

import os
import re
from pathlib import Path

import pytest

from app.db.images import ImageStore
from app.errors import StorageError

NAME = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}\.(jpg|png|webp)")


@pytest.mark.parametrize(("image_type", "ext"), [("jpeg", "jpg"), ("png", "png"), ("webp", "webp")])
def test_save_read_remove(tmp_path: Path, image_type: str, ext: str) -> None:
    store = ImageStore(str(tmp_path / "uploads"))

    name = store.save(b"bytes", image_type)

    assert NAME.fullmatch(name) and name.endswith(ext)
    assert store.read(name) == b"bytes"
    assert os.listdir(tmp_path / "uploads") == [name]  # no temp file left behind
    store.remove(name)
    assert os.listdir(tmp_path / "uploads") == []
    store.remove(name)  # already gone: fine


def test_names_are_unique(tmp_path: Path) -> None:
    store = ImageStore(str(tmp_path))

    assert store.save(b"a", "png") != store.save(b"a", "png")


@pytest.mark.parametrize("name", ["../outside.jpg", "/etc/passwd", "sub/x.jpg", "..", ""])
def test_paths_stay_inside_the_upload_dir(tmp_path: Path, name: str) -> None:
    store = ImageStore(str(tmp_path / "uploads"))
    (tmp_path / "uploads").mkdir()

    for use in (store.read, store.remove):
        with pytest.raises(StorageError):
            use(name)


def test_missing_file_is_a_storage_error_on_read(tmp_path: Path) -> None:
    with pytest.raises(StorageError):
        ImageStore(str(tmp_path)).read("0b7c.jpg")


def test_unwritable_dir_is_a_storage_error_and_leaves_no_temp_file(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("", encoding="utf-8")

    with pytest.raises(StorageError):
        ImageStore(str(tmp_path / "file")).save(b"x", "jpeg")


def test_failed_move_removes_the_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(src: str, dst: object) -> None:
        raise OSError("no")

    monkeypatch.setattr(os, "replace", fail)

    with pytest.raises(StorageError):
        ImageStore(str(tmp_path)).save(b"x", "jpeg")
    assert os.listdir(tmp_path) == []


def test_failed_unlink_is_a_storage_error(tmp_path: Path) -> None:
    store = ImageStore(str(tmp_path))
    name = store.save(b"x", "jpeg")
    (tmp_path / name).unlink()
    (tmp_path / name).mkdir()  # a directory can't be unlinked

    with pytest.raises(StorageError):
        store.remove(name)

"""Receipt images in UPLOAD_DIR (persistence.md).

File names are random (`<uuid4>.<ext>`), never the uploaded name. Every path is
resolved and must stay inside UPLOAD_DIR. A write goes to a temp file first and is
moved in with `os.replace`, so a reader never sees half a file. `OSError` becomes
`StorageError`.
"""

import contextlib
import os
import tempfile
import uuid
from pathlib import Path

from app.errors import StorageError

EXTENSIONS = {"jpeg": "jpg", "png": "png", "webp": "webp"}


class ImageStore:
    def __init__(self, upload_dir: str) -> None:
        self._upload_dir = upload_dir

    def _root(self) -> Path:
        return Path(self._upload_dir).resolve()

    def _path(self, name: str) -> Path:
        root = self._root()
        path = (root / name).resolve()
        if path.parent != root:
            raise StorageError("The image path is outside the upload directory.")
        return path

    def save(self, data: bytes, image_type: str) -> str:
        """Store the bytes under a new random name and return that name."""
        name = f"{uuid.uuid4()}.{EXTENSIONS[image_type]}"
        target = self._path(name)
        tmp_name: str | None = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=target.parent, prefix=".upload-", suffix=".tmp", delete=False
            ) as tmp:
                tmp_name = tmp.name
                tmp.write(data)
            os.replace(tmp_name, target)
        except OSError as exc:
            if tmp_name is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)
            raise StorageError("The image could not be stored.") from exc
        return name

    def read(self, name: str) -> bytes:
        try:
            return self._path(name).read_bytes()
        except OSError as exc:
            raise StorageError("The image could not be read.") from exc

    def remove(self, name: str) -> None:
        """Delete the file; one that is already gone is fine."""
        try:
            self._path(name).unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError("The image could not be removed.") from exc

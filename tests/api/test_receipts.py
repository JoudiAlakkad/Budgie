"""`/api/receipts`: upload, list, get, image and delete (contracts/api-endpoints.md#receipts)."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db.images import ImageStore
from app.db.repositories.receipts import ReceiptRepository
from app.db.session import Database
from app.errors import StorageError
from app.main import create_app
from app.services.dependencies import (
    database_for,
    dispose_databases,
    get_database,
    get_image_store,
    get_receipt_pipeline,
)
from tests.api.helpers import JPEG, PNG, WEBP, NoopPipeline, upload


@pytest.fixture(autouse=True)
def _no_extraction(client: TestClient) -> None:
    """These tests cover the receipt routes; the extraction has its own tests."""
    client.app.dependency_overrides[get_receipt_pipeline] = NoopPipeline  # type: ignore[attr-defined]


def uploads(settings: Settings) -> list[str]:
    return sorted(os.listdir(settings.upload_dir))


@pytest.mark.parametrize(
    ("data", "media_type"),
    [(JPEG, "image/jpeg"), (PNG, "image/png"), (WEBP, "image/webp")],
    ids=["jpeg", "png", "webp"],
)
def test_upload_returns_202_uploaded_and_stores_the_image(
    client: TestClient, settings: Settings, data: bytes, media_type: str
) -> None:
    response = upload(client, data, filename="../../Kassenbon Müller.bin")

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "uploaded"
    assert (body["error"], body["error_detail"], body["expense"]) == (None, None, None)
    assert body["uploaded_at"].endswith("Z")
    [name] = uploads(settings)
    assert "Kassenbon" not in name  # a random name, never the uploaded one
    image = client.get(f"/api/receipts/{body['id']}/image")
    assert image.status_code == 200
    assert image.headers["content-type"] == media_type
    assert image.content == data


@pytest.mark.parametrize(
    "data",
    [b"", b"GIF89a....", b"%PDF-1.7", b"RIFF\x00\x00\x00\x00WAVEfmt ", b"\xff\xd8", b"<svg/>"],
    ids=["empty", "gif", "pdf", "riff-wave", "short-jpeg", "svg"],
)
def test_upload_checks_the_type_by_its_first_bytes(
    client: TestClient, settings: Settings, data: bytes
) -> None:
    # The declared content type doesn't matter; only the bytes do.
    response = upload(client, data, content_type="image/jpeg")

    assert response.status_code == 422
    assert response.json()["error"] == "unsupported_file"
    assert uploads(settings) == []


def test_upload_above_max_upload_mb_is_413(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'budgie.db'}",
        upload_dir=str(tmp_path / "uploads"),
        frontend_dir="",
        max_upload_mb=1,
    )
    app = create_app(settings)
    app.dependency_overrides[get_receipt_pipeline] = NoopPipeline
    with TestClient(app) as client:
        too_big = upload(client, JPEG + b"\x00" * (1024 * 1024))
        just_fits = upload(client, JPEG + b"\x00" * (1024 * 1024 - len(JPEG)))
    dispose_databases()

    assert too_big.status_code == 413
    assert too_big.json()["error"] == "file_too_large"
    assert just_fits.status_code == 202
    assert len(uploads(settings)) == 1


def test_upload_without_a_file_is_a_validation_error(client: TestClient) -> None:
    response = client.post("/api/receipts", files={"other": ("a.jpg", JPEG, "image/jpeg")})

    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"


class FailingDatabase(Database):
    """A database whose every transaction fails."""

    @contextmanager
    def transaction(self) -> Iterator[object]:  # type: ignore[override]
        raise StorageError()
        yield  # pragma: no cover


def test_upload_removes_the_image_when_the_row_cannot_be_created(
    client: TestClient, settings: Settings
) -> None:
    client.app.dependency_overrides[get_database] = lambda: FailingDatabase("sqlite://")  # type: ignore[attr-defined]

    response = upload(client, JPEG)

    assert response.status_code == 500
    assert response.json()["error"] == "storage_error"
    assert uploads(settings) == []


def test_list_is_newest_first_and_filters_by_status(client: TestClient, settings: Settings) -> None:
    ids = [upload(client, JPEG).json()["id"] for _ in range(3)]
    with database_for(settings.database_url).transaction() as session:
        ReceiptRepository(session).transition(ids[1], "uploaded", "failed", error="llm_timeout")

    listed = client.get("/api/receipts")
    failed = client.get("/api/receipts", params={"status": "failed"})

    assert listed.status_code == 200
    assert [r["id"] for r in listed.json()] == ids[::-1]
    [only] = failed.json()
    assert only["id"] == ids[1]
    assert only["error"] == "llm_timeout"
    assert only["error_detail"].startswith("The model did not answer in time.")
    assert client.get("/api/receipts", params={"status": "confirmed"}).json() == []


def test_list_rejects_an_unknown_status(client: TestClient) -> None:
    response = client.get("/api/receipts", params={"status": "done"})

    assert response.status_code == 422
    assert response.json()["fields"][0]["field"] == "query.status"


def test_get_returns_the_receipt(client: TestClient) -> None:
    created = upload(client, PNG).json()

    response = client.get(f"/api/receipts/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/receipts/999"),
        ("GET", "/api/receipts/999/image"),
        ("DELETE", "/api/receipts/999"),
    ],
)
def test_unknown_receipt_is_404(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, path)

    assert response.status_code == 404
    assert response.json() == {
        "error": "not_found",
        "detail": "Receipt 999 does not exist.",
        "fields": None,
    }


def test_missing_image_file_is_a_storage_error(client: TestClient, settings: Settings) -> None:
    receipt_id = upload(client, JPEG).json()["id"]
    [name] = uploads(settings)
    (Path(settings.upload_dir) / name).unlink()

    response = client.get(f"/api/receipts/{receipt_id}/image")

    assert response.status_code == 500
    assert response.json()["error"] == "storage_error"


def test_delete_removes_rows_and_file(client: TestClient, settings: Settings) -> None:
    keep = upload(client, JPEG).json()["id"]
    gone = upload(client, PNG).json()["id"]

    response = client.delete(f"/api/receipts/{gone}")

    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"/api/receipts/{gone}").status_code == 404
    assert [r["id"] for r in client.get("/api/receipts").json()] == [keep]
    [name] = uploads(settings)
    assert name.endswith(".jpg")


class StuckImageStore(ImageStore):
    def remove(self, name: str) -> None:
        raise StorageError("The image could not be removed.")


def test_delete_answers_204_when_the_file_cannot_be_removed(
    client: TestClient, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    receipt_id = upload(client, JPEG).json()["id"]
    client.app.dependency_overrides[get_image_store] = lambda: StuckImageStore(settings.upload_dir)  # type: ignore[attr-defined]

    response = client.delete(f"/api/receipts/{receipt_id}")

    assert response.status_code == 204
    assert client.get(f"/api/receipts/{receipt_id}").status_code == 404
    assert len(uploads(settings)) == 1  # an orphan file is harmless
    assert f"Receipt {receipt_id}: image file not removed (StorageError)" in caplog.messages


def test_delete_with_a_file_already_gone_is_204(client: TestClient, settings: Settings) -> None:
    receipt_id = upload(client, JPEG).json()["id"]
    [name] = uploads(settings)
    (Path(settings.upload_dir) / name).unlink()

    assert client.delete(f"/api/receipts/{receipt_id}").status_code == 204


def test_broken_database_is_500_storage_error(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'file' / 'budgie.db'}",
        upload_dir=str(tmp_path / "uploads"),
        frontend_dir="",
    )
    with TestClient(create_app(settings)) as client:
        responses = [
            client.get("/api/receipts"),
            client.get("/api/receipts/1"),
            upload(client, JPEG),
        ]
        health = client.get("/api/health").json()
    dispose_databases()

    for response in responses:
        assert response.status_code == 500
        assert response.json() == {
            "error": "storage_error",
            "detail": "The data could not be read or written.",
            "fields": None,
        }
    assert health["db"] == "error"
    assert os.listdir(tmp_path / "uploads") == []

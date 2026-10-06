"""Receipt use cases: upload, list, get, image and delete (contracts/api-endpoints.md#receipts).

The extraction itself runs in `receipt_pipeline` as a background task.
"""

import datetime as dt
import logging
from typing import BinaryIO

from app.db.images import ImageStore
from app.db.repositories.expenses import ExpenseRepository
from app.db.repositories.receipts import ReceiptRepository
from app.db.session import Database
from app.errors import (
    ExtractionInterrupted,
    FileTooLarge,
    InvalidState,
    LLMError,
    LLMTimeout,
    LLMUnavailable,
    MalformedOutput,
    NotAReceipt,
    NotFound,
    StorageError,
    UnreadableImage,
    UnsupportedFile,
)
from app.services.views import ImageView, ReceiptView, receipt_view

logger = logging.getLogger(__name__)

# Retry is allowed from every failed receipt, including unreadable_image, and from
# extracted (contracts/receipt-lifecycle.md).
RETRY_FROM = ("failed", "extracted")
MEDIA_TYPES = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# `Receipt.error_detail` is derived, not stored, so a wording fix reaches old rows too
# (persistence.md). The texts are the `detail`s of the extraction errors.
ERROR_DETAIL: dict[str, str] = {
    cls.code: cls.detail
    for cls in (
        LLMUnavailable,
        LLMTimeout,
        LLMError,
        MalformedOutput,
        NotAReceipt,
        UnreadableImage,
        ExtractionInterrupted,
    )
}


def utc_now() -> dt.datetime:
    """Naive UTC, as stored (persistence.md)."""
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def image_type(data: bytes) -> str | None:
    """`jpeg`, `png` or `webp` by the first bytes, else None."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(PNG_SIGNATURE):
        return "png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def error_detail(code: str | None) -> str | None:
    return ERROR_DETAIL.get(code) if code is not None else None


class ReceiptService:
    def __init__(self, db: Database, images: ImageStore, max_upload_mb: int) -> None:
        self._db = db
        self._images = images
        self._max_bytes = max_upload_mb * 1024 * 1024

    def upload(self, stream: BinaryIO) -> ReceiptView:
        """Store the image and create the receipt with status `uploaded`.

        Raises `FileTooLarge` above MAX_UPLOAD_MB and `UnsupportedFile` unless the first
        bytes say jpeg, png or webp. If the row can't be created, the file is removed again.
        """
        try:
            data = stream.read(self._max_bytes + 1)
        except OSError as exc:
            raise StorageError("The upload could not be read.") from exc
        if len(data) > self._max_bytes:
            raise FileTooLarge()
        kind = image_type(data)
        if kind is None:
            raise UnsupportedFile()
        name = self._images.save(data, kind)
        try:
            with self._db.transaction() as session:
                receipt = ReceiptRepository(session).create(name, kind, utc_now())
        except BaseException:
            self._remove_image(None, name)
            raise
        logger.info("Receipt %d uploaded (%s)", receipt.id, kind)
        return receipt_view(receipt, None, None)

    def list(self, status: str | None = None) -> list[ReceiptView]:
        """Newest first, each with its expense once one exists."""
        with self._db.transaction() as session:
            receipts = ReceiptRepository(session).list(status)
            expenses = ExpenseRepository(session).by_receipts(r.id for r in receipts)
        return [receipt_view(r, expenses.get(r.id), error_detail(r.error)) for r in receipts]

    def get(self, receipt_id: int) -> ReceiptView:
        with self._db.transaction() as session:
            receipt = ReceiptRepository(session).get(receipt_id)
            if receipt is None:
                raise NotFound(f"Receipt {receipt_id} does not exist.")
            expense = ExpenseRepository(session).get_by_receipt(receipt_id)
        return receipt_view(receipt, expense, error_detail(receipt.error))

    def image(self, receipt_id: int) -> ImageView:
        with self._db.transaction() as session:
            receipt = ReceiptRepository(session).get(receipt_id)
        if receipt is None:
            raise NotFound(f"Receipt {receipt_id} does not exist.")
        return ImageView(self._images.read(receipt.image_path), MEDIA_TYPES[receipt.image_type])

    def retry(self, receipt_id: int) -> ReceiptView:
        """`failed` or `extracted` -> `uploaded`, in one transaction; the caller queues the task.

        The unconfirmed expense is deleted, and `error`, `model_name`, `prompt_version`,
        `latency_ms` and `raw_model_output` are cleared. Raises `InvalidState` otherwise.
        """
        with self._db.transaction() as session:
            receipts = ReceiptRepository(session)
            current = receipts.get(receipt_id)
            if current is None:
                raise NotFound(f"Receipt {receipt_id} does not exist.")
            if not receipts.transition(
                receipt_id,
                RETRY_FROM,
                "uploaded",
                error=None,
                model_name=None,
                prompt_version=None,
                latency_ms=None,
                raw_model_output=None,
            ):
                raise InvalidState(
                    f"Receipt {receipt_id} is {current.status}; only a failed or extracted "
                    "receipt can be extracted again."
                )
            ExpenseRepository(session).delete_unconfirmed_for_receipt(receipt_id)
            receipt = receipts.get(receipt_id)
        assert receipt is not None
        logger.info("Receipt %d queued for extraction again", receipt_id)
        return receipt_view(receipt, None, None)

    def delete(self, receipt_id: int) -> None:
        """Delete in any status: the rows first, then the file.

        An extraction still running finds the row gone and discards its result. A file
        that can't be removed is logged and ignored; an orphan file is harmless.
        """
        with self._db.transaction() as session:
            receipt = ReceiptRepository(session).delete(receipt_id)
        if receipt is None:
            raise NotFound(f"Receipt {receipt_id} does not exist.")
        self._remove_image(receipt_id, receipt.image_path)
        logger.info("Receipt %d deleted", receipt_id)

    def _remove_image(self, receipt_id: int | None, name: str) -> None:
        try:
            self._images.remove(name)
        except StorageError as exc:
            logger.warning(
                "Receipt %s: image file not removed (%s)",
                receipt_id,
                type(exc.__cause__ or exc).__name__,
            )


def reset_interrupted(db: Database) -> int:
    """At startup: `uploaded` and `extracting` receipts become `failed` with `interrupted`.

    Their background tasks died with the previous process (decision 0007). Never raises:
    a failure is logged by type name and the app starts anyway.
    """
    try:
        with db.transaction() as session:
            count = ReceiptRepository(session).reset_interrupted()
    except Exception as exc:
        logger.error("Startup reset of interrupted receipts failed (%s)", type(exc).__name__)
        return 0
    if count:
        logger.warning("Startup reset %d interrupted receipts to failed", count)
    return count

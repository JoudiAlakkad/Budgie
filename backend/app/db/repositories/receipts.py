"""Receipt rows. Status changes are guarded updates (`WHERE id = :id AND status IN :from`)."""

import datetime as dt
from collections.abc import Collection
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import ReceiptRow
from app.db.records import ReceiptRecord

# Columns a transition may set besides `status`.
TRANSITION_FIELDS = frozenset(
    {"error", "model_name", "prompt_version", "latency_ms", "raw_model_output"}
)
INTERRUPTED_FROM = ("uploaded", "extracting")


def to_record(row: ReceiptRow) -> ReceiptRecord:
    return ReceiptRecord(
        id=row.id,
        image_path=row.image_path,
        image_type=row.image_type,
        status=row.status,
        error=row.error,
        uploaded_at=row.uploaded_at,
        model_name=row.model_name,
        prompt_version=row.prompt_version,
        latency_ms=row.latency_ms,
        raw_model_output=row.raw_model_output,
    )


class ReceiptRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, image_path: str, image_type: str, uploaded_at: dt.datetime) -> ReceiptRecord:
        """A new receipt with status `uploaded`."""
        row = ReceiptRow(
            image_path=image_path,
            image_type=image_type,
            status="uploaded",
            uploaded_at=uploaded_at,
        )
        self._session.add(row)
        self._session.flush()
        return to_record(row)

    def get(self, receipt_id: int) -> ReceiptRecord | None:
        row = self._session.get(ReceiptRow, receipt_id)
        return to_record(row) if row is not None else None

    def list(self, status: str | None = None) -> list[ReceiptRecord]:
        """Newest first (upload time, then id)."""
        query = select(ReceiptRow).order_by(ReceiptRow.uploaded_at.desc(), ReceiptRow.id.desc())
        if status is not None:
            query = query.where(ReceiptRow.status == status)
        return [to_record(row) for row in self._session.scalars(query)]

    def transition(
        self, receipt_id: int, from_: str | Collection[str], to: str, **fields: Any
    ) -> bool:
        """Set `status = to` (and `fields`) only if the status is `from_`; True if it was.

        The caller decides what a False means: the receipt is gone, or another action
        changed its status first.
        """
        unknown = set(fields) - TRANSITION_FIELDS
        if unknown:
            raise ValueError(f"not a transition field: {sorted(unknown)}")
        sources = [from_] if isinstance(from_, str) else list(from_)
        statement = (
            update(ReceiptRow)
            .where(ReceiptRow.id == receipt_id, ReceiptRow.status.in_(sources))
            .values(status=to, **fields)
        )
        result = self._session.execute(statement)
        return result.rowcount == 1  # type: ignore[attr-defined]

    def reset_interrupted(self) -> int:
        """`uploaded` and `extracting` become `failed` with `interrupted`; returns the count."""
        statement = (
            update(ReceiptRow)
            .where(ReceiptRow.status.in_(INTERRUPTED_FROM))
            .values(status="failed", error="interrupted")
        )
        return self._session.execute(statement).rowcount  # type: ignore[attr-defined]

    def delete(self, receipt_id: int) -> ReceiptRecord | None:
        """Delete the receipt with its expense and line items (ORM cascade).

        Returns the deleted record, so the caller can remove the image, or None if the
        receipt didn't exist.
        """
        row = self._session.get(ReceiptRow, receipt_id)
        if row is None:
            return None
        record = to_record(row)
        self._session.delete(row)
        self._session.flush()
        return record

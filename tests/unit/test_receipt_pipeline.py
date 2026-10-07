"""`ReceiptPipeline` directly: the lock, guarded transitions and storage errors."""

import datetime as dt
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.ai.extractor import ExtractionResult
from app.ai.schema import ReceiptExtraction
from app.db.images import ImageStore
from app.db.records import ReceiptRecord
from app.db.repositories.receipts import ReceiptRepository
from app.db.session import Database
from app.errors import LLMTimeout, StorageError
from app.services import receipt_pipeline
from app.services.categorization import LookupCategorizer
from app.services.receipt_pipeline import ReceiptPipeline

TODAY = dt.date(2026, 10, 5)
NOW = dt.datetime(2026, 10, 5, 12, 0)
VALID = ReceiptExtraction.model_validate(
    {
        "is_receipt": True,
        "merchant": "Beispiel Markt",
        "date": "2026-10-01",
        "currency": "EUR",
        "line_items": [{"description": "BROT", "qty": 1, "amount": 2.49}],
        "total": 2.49,
        "payment_method": None,
        "unreadable_fields": [],
    }
)


class FakeExtractor:
    model = "fake-model"
    prompt_version = "v2"

    def __init__(self, outcome: ExtractionResult | Exception, gate: threading.Event | None = None):
        self.outcome = outcome
        self.gate = gate
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def extract(self, image: bytes, mime: str) -> ExtractionResult:
        with self._lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.gate is not None:
                assert self.gate.wait(5)
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return self.outcome
        finally:
            with self._lock:
                self.active -= 1


def ok_result() -> ExtractionResult:
    return ExtractionResult(VALID, "fake-model", "v2", 0.25, VALID.model_dump_json(), False, 10)


class FlakyDatabase(Database):
    """Fails the transactions whose 1-based number is in `failing`."""

    def __init__(self, url: str, failing: set[int]) -> None:
        super().__init__(url)
        self.failing = failing
        self.count = 0

    @contextmanager
    def transaction(self) -> Iterator[object]:  # type: ignore[override]
        self.count += 1
        if self.count in self.failing:
            raise StorageError()
        with super().transaction() as session:
            yield session


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    db = Database(f"sqlite:///{tmp_path / 'budgie.db'}")
    db.init_db()
    db.dispose()
    return db.url


@pytest.fixture
def images(tmp_path: Path) -> ImageStore:
    return ImageStore(str(tmp_path / "uploads"))


def new_receipt(db: Database, images: ImageStore) -> int:
    name = images.save(b"\xff\xd8\xffjpeg", "jpeg")
    with db.transaction() as session:
        return ReceiptRepository(session).create(name, "jpeg", NOW).id


def get(db: Database, receipt_id: int) -> ReceiptRecord | None:
    with db.transaction() as session:
        return ReceiptRepository(session).get(receipt_id)


def pipeline(db: Database, images: ImageStore, extractor: FakeExtractor) -> ReceiptPipeline:
    return ReceiptPipeline(db, images, lambda: extractor, LookupCategorizer(), lambda: TODAY)


def test_the_lock_is_taken_before_uploaded_to_extracting(db_url: str, images: ImageStore) -> None:
    db = Database(db_url)
    receipt_id = new_receipt(db, images)
    task = pipeline(db, images, FakeExtractor(ok_result()))

    with receipt_pipeline._extraction_lock:
        thread = threading.Thread(target=task.run, args=(receipt_id,))
        thread.start()
        thread.join(0.2)
        assert thread.is_alive()
        record = get(db, receipt_id)
        assert record is not None and record.status == "uploaded"  # queued
    thread.join(5)

    record = get(db, receipt_id)
    assert record is not None and record.status == "extracted"
    db.dispose()


def test_only_one_extraction_runs_at_a_time(db_url: str, images: ImageStore) -> None:
    db = Database(db_url)
    first, second = new_receipt(db, images), new_receipt(db, images)
    gate = threading.Event()
    extractor = FakeExtractor(ok_result(), gate)
    task = pipeline(db, images, extractor)
    threads = [threading.Thread(target=task.run, args=(rid,)) for rid in (first, second)]

    for thread in threads:
        thread.start()
    for _ in range(100):
        if extractor.calls:
            break
        threading.Event().wait(0.01)
    statuses = sorted(r.status for r in (get(db, first), get(db, second)) if r)
    gate.set()
    for thread in threads:
        thread.join(5)

    assert statuses == ["extracting", "uploaded"]
    assert extractor.max_active == 1
    assert extractor.calls == 2
    db.dispose()


def test_a_receipt_that_is_not_uploaded_is_left_alone(db_url: str, images: ImageStore) -> None:
    db = Database(db_url)
    receipt_id = new_receipt(db, images)
    with db.transaction() as session:
        ReceiptRepository(session).transition(receipt_id, "uploaded", "failed", error="llm_error")
    extractor = FakeExtractor(ok_result())

    pipeline(db, images, extractor).run(receipt_id)
    pipeline(db, images, extractor).run(999)  # deleted before the task started

    assert extractor.calls == 0
    record = get(db, receipt_id)
    assert record is not None and (record.status, record.error) == ("failed", "llm_error")
    db.dispose()


@pytest.mark.parametrize(
    ("outcome", "code", "outcome_write"),
    [
        # 2: uploaded -> extracting, 3: the lookup read (F07), 4: the outcome
        (ok_result(), "interrupted", 4),
        # 2: uploaded -> extracting, 3: the outcome (a failure needs no lookup)
        (LLMTimeout("slow", latency_s=1.0), "llm_timeout", 3),
    ],
    ids=["success", "failure"],
)
def test_a_storage_error_on_the_outcome_write_marks_the_receipt_failed(
    db_url: str,
    images: ImageStore,
    caplog: pytest.LogCaptureFixture,
    outcome: ExtractionResult | Exception,
    code: str,
    outcome_write: int,
) -> None:
    db = FlakyDatabase(db_url, failing=set())
    receipt_id = new_receipt(db, images)  # transaction 1
    db.failing = {outcome_write}  # the next one is the second try

    pipeline(db, images, FakeExtractor(outcome)).run(receipt_id)

    record = get(db, receipt_id)
    assert record is not None
    assert (record.status, record.error, record.raw_model_output) == ("failed", code, None)
    assert (
        f"Receipt {receipt_id}: outcome not stored (StorageError); marking it failed"
        in caplog.messages
    )
    db.dispose()


def test_when_the_second_try_fails_too_the_receipt_stays_extracting(
    db_url: str, images: ImageStore, caplog: pytest.LogCaptureFixture
) -> None:
    db = FlakyDatabase(db_url, failing=set())
    receipt_id = new_receipt(db, images)
    db.failing = {4, 5}  # 3 is the lookup read, 4 the outcome, 5 the second try

    pipeline(db, images, FakeExtractor(ok_result())).run(receipt_id)  # never raises

    record = get(db, receipt_id)
    assert record is not None and record.status == "extracting"  # the startup reset's job
    assert (
        f"Receipt {receipt_id}: not marked failed (StorageError); the startup reset will"
        in caplog.messages
    )
    db.dispose()


def test_a_storage_error_on_the_lookup_read_fails_the_receipt_as_interrupted(
    db_url: str, images: ImageStore, caplog: pytest.LogCaptureFixture
) -> None:
    """The read of the lookup table and the duplicate candidates (decision 0020) runs
    after the model call; if it fails, the receipt is `interrupted`, with the model call's
    latency kept (the raw output is kept only for malformed_output and not_a_receipt)."""
    db = FlakyDatabase(db_url, failing=set())
    receipt_id = new_receipt(db, images)  # transaction 1
    db.failing = {3}  # 2: uploaded -> extracting, 3: the lookup read, 4: the outcome

    pipeline(db, images, FakeExtractor(ok_result())).run(receipt_id)

    record = get(db, receipt_id)
    assert record is not None
    assert (record.status, record.error, record.raw_model_output) == ("failed", "interrupted", None)
    assert record.latency_ms == 250
    assert f"Receipt {receipt_id}: failed with interrupted (StorageError)" in caplog.messages
    db.dispose()


def test_a_non_receipt_does_not_read_the_lookup_table(db_url: str, images: ImageStore) -> None:
    """The plausibility rule needs no categories, so it runs before the lookup read."""
    implausible = VALID.model_copy(update={"merchant": None, "total": None})
    result = ExtractionResult(
        implausible, "fake-model", "v2", 0.25, implausible.model_dump_json(), False, 10
    )
    db = FlakyDatabase(db_url, failing=set())
    receipt_id = new_receipt(db, images)  # transaction 1

    pipeline(db, images, FakeExtractor(result)).run(receipt_id)

    assert db.count == 3  # 2: uploaded -> extracting, 3: the outcome; no lookup read
    record = get(db, receipt_id)
    assert record is not None and (record.status, record.error) == ("failed", "not_a_receipt")
    db.dispose()


def test_a_storage_error_before_the_start_marks_the_receipt_failed(
    db_url: str, images: ImageStore, caplog: pytest.LogCaptureFixture
) -> None:
    db = FlakyDatabase(db_url, failing=set())
    receipt_id = new_receipt(db, images)  # transaction 1
    db.failing = {2}  # 2: uploaded -> extracting, 3: the try to mark it failed
    extractor = FakeExtractor(ok_result())

    pipeline(db, images, extractor).run(receipt_id)

    assert extractor.calls == 0
    record = get(db, receipt_id)
    assert record is not None
    assert (record.status, record.error) == ("failed", "interrupted")
    assert (
        f"Receipt {receipt_id}: extraction task stopped (StorageError); marking it failed"
        in caplog.messages
    )
    db.dispose()


def test_when_marking_it_failed_fails_too_the_receipt_stays_uploaded(
    db_url: str, images: ImageStore, caplog: pytest.LogCaptureFixture
) -> None:
    db = FlakyDatabase(db_url, failing=set())
    receipt_id = new_receipt(db, images)
    db.failing = {2, 3}
    extractor = FakeExtractor(ok_result())

    pipeline(db, images, extractor).run(receipt_id)  # never raises

    assert extractor.calls == 0
    record = get(db, receipt_id)
    assert record is not None and record.status == "uploaded"  # until the next restart
    assert (
        f"Receipt {receipt_id}: not marked failed (StorageError); the startup reset will"
        in caplog.messages
    )
    db.dispose()


def test_model_name_prompt_version_and_latency_are_stored(db_url: str, images: ImageStore) -> None:
    db = Database(db_url)
    receipt_id = new_receipt(db, images)

    pipeline(db, images, FakeExtractor(ok_result())).run(receipt_id)

    record = get(db, receipt_id)
    assert record is not None
    assert (record.model_name, record.prompt_version, record.latency_ms) == (
        "fake-model",
        "v2",
        250,
    )
    db.dispose()


def test_today_is_read_inside_the_task_when_the_rules_run(db_url: str, images: ImageStore) -> None:
    db = Database(db_url)
    receipt_id = new_receipt(db, images)
    extractor = FakeExtractor(ok_result())
    events: list[str] = []
    real_extract = extractor.extract

    def extract(image: bytes, mime: str) -> ExtractionResult:
        events.append("extract")
        return real_extract(image, mime)

    def today() -> dt.date:
        events.append("today")
        return TODAY

    extractor.extract = extract  # type: ignore[method-assign]
    task = ReceiptPipeline(db, images, lambda: extractor, LookupCategorizer(), today)
    assert events == []  # building the task (at request time) doesn't read the clock

    task.run(receipt_id)

    assert events == ["extract", "today"]
    db.dispose()

"""`process_extraction`, the pipeline's post-extraction step as one public function (0028).

The pipeline delegates to it; the evaluation calls it directly with the seed table. These
tests pin that the two give the same result, and the parts the evaluation relies on.
"""

import datetime as dt
import logging
import re
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

from app.ai.client import LLMClient
from app.ai.extractor import ExtractionResult, Extractor
from app.ai.prompts import load_prompts
from app.ai.schema import LineItem, ReceiptExtraction
from app.db.images import ImageStore
from app.db.repositories.expenses import ExpenseRepository
from app.db.repositories.item_categories import ItemCategoryRepository
from app.db.repositories.receipts import ReceiptRepository
from app.db.session import Database
from app.domain.categorize import CategoryTable
from app.domain.duplicates import ExpenseKey
from app.domain.non_items import ItemLine
from app.errors import ExtractionError, ExtractionInterrupted, NotAReceipt, StorageError
from app.services.categorization import LookupCategorizer
from app.services.item_categories import sync_seed
from app.services.receipt_pipeline import (
    Extracted,
    GuardCounts,
    ReceiptPipeline,
    kept_items,
    process_extraction,
    redact_extraction,
)
from tests.recorded import case_names, load_case, replay

TODAY = dt.date(2026, 10, 5)
NOW = dt.datetime(2026, 10, 5, 12, 0)
JPEG = b"\xff\xd8\xffjpeg"


def result_of(extraction: ReceiptExtraction, latency_s: float = 0.25) -> ExtractionResult:
    return ExtractionResult(
        extraction, "fake-model", "v3", latency_s, extraction.model_dump_json(), False, 10
    )


def extraction(**fields: object) -> ReceiptExtraction:
    base = {
        "is_receipt": True,
        "merchant": "Beispiel Markt",
        "date": "2026-10-01",
        "currency": "EUR",
        "line_items": [{"description": "BROT", "qty": 1, "amount": 2.49}],
        "total": 2.49,
        "payment_method": None,
        "unreadable_fields": [],
    }
    return ReceiptExtraction.model_validate({**base, **fields})


class Lookups:
    """A lookup loader that records its calls."""

    def __init__(self, table: CategoryTable | None = None, error: Exception | None = None):
        self.table: CategoryTable = table or {}
        self.error = error
        self.calls: list[tuple[dt.date | None, Decimal | None]] = []

    def __call__(
        self, day: dt.date | None, total: Decimal | None
    ) -> tuple[CategoryTable, list[ExpenseKey]]:
        self.calls.append((day, total))
        if self.error is not None:
            raise self.error
        return self.table, []


def run(result: ExtractionResult, lookups: Lookups) -> Extracted:
    return process_extraction(
        7, result, lookups=lookups, categorizer=LookupCategorizer(), today=lambda: TODAY
    )


def test_a_non_receipt_never_calls_lookups() -> None:
    lookups = Lookups()
    implausible = extraction(merchant=None, total=None)

    with pytest.raises(NotAReceipt):
        run(result_of(implausible), lookups)

    assert lookups.calls == []


def test_a_storage_error_from_lookups_becomes_interrupted_with_the_latency_kept() -> None:
    lookups = Lookups(error=StorageError())

    with pytest.raises(ExtractionInterrupted) as caught:
        run(result_of(extraction(), latency_s=1.5), lookups)

    assert caught.value.latency_s == 1.5
    assert caught.value.raw_output is None
    assert caught.value.reason == "StorageError"
    assert isinstance(caught.value.__cause__, StorageError)


def test_lookups_get_the_parsed_date_and_the_guarded_total() -> None:
    lookups = Lookups()
    paid = extraction(
        line_items=[
            {"description": "BROT", "qty": None, "amount": 2.49},
            {"description": "ZU ZAHLEN", "qty": None, "amount": 2.49},
        ],
        total=219.0,
    )

    run(result_of(paid), lookups)

    assert lookups.calls == [(dt.date(2026, 10, 1), Decimal("2.49"))]


def test_the_guard_counts_match_the_logged_counts(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app")
    result = extract_case("quantity_lines_and_paid_total")
    assert isinstance(result, ExtractionResult)

    outcome = run(result, Lookups())

    logged = {
        name: int(match.group(1))
        for message in caplog.messages
        for name, pattern in (
            ("dropped", r"Receipt 7: dropped (\d+) non-item lines"),
            ("merged", r"Receipt 7: merged (\d+) quantity lines"),
            ("negated", r"Receipt 7: negated (\d+) deposit returns"),
        )
        if (match := re.fullmatch(pattern, message))
    }
    paid = "Receipt 7: took the total from the paid line" in caplog.messages
    assert outcome.guard == GuardCounts(
        dropped=logged.get("dropped", 0),
        merged=logged.get("merged", 0),
        negated=logged.get("negated", 0),
        paid_total=paid,
    )
    # The fixture exercises the guard: something was dropped, merged and the paid total used.
    assert outcome.guard.dropped > 0 and outcome.guard.merged > 0 and outcome.guard.paid_total


@pytest.mark.parametrize(
    ("lines", "total", "expected"),
    [
        ([("BROT", "2.49")], "2.49", GuardCounts()),
        ([("BROT", "2.49"), ("SUMME", "2.49")], "2.49", GuardCounts(dropped=1)),
        ([("BROT", "2.49"), ("LEERGUT", "0.25")], "2.24", GuardCounts(negated=1)),
        ([("2 x 0,95", "1.90"), ("BROETCHEN", "0.95")], "1.90", GuardCounts(merged=1)),
        (
            [("BROT", "2.49"), ("ZU ZAHLEN", "2.49")],
            "219.00",
            GuardCounts(dropped=1, paid_total=True),
        ),
    ],
)
def test_kept_items_returns_its_counts(
    lines: list[tuple[str, str]], total: str, expected: GuardCounts
) -> None:
    items = [ItemLine(description, Decimal(amount), None) for description, amount in lines]

    _, _, counts = kept_items(1, items, Decimal(total))

    assert counts == expected


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"merchant": "Beispiel Markt\nMusterstrasse 12, 44137 Dortmund"},
        {
            "line_items": [
                {"description": "KARTE 4111111111111111", "qty": None, "amount": 2.49},
                {"description": "Beleg-Nr. 4711 0815", "qty": None, "amount": 1.0},
            ]
        },
        {"date": "01.10.2026 Bon-Nr: 4711", "currency": "€"},
        {"merchant": None, "date": None, "currency": None, "line_items": []},
    ],
)
def test_redact_extraction_is_idempotent(fields: dict[str, object]) -> None:
    once = redact_extraction(extraction(**fields))

    assert redact_extraction(once) == once


def test_redact_extraction_redacts_the_free_text() -> None:
    raw = extraction(
        merchant="Beispiel Markt\nMusterstrasse 12, 44137 Dortmund",
        line_items=[{"description": "KARTE 4111111111111111", "qty": None, "amount": 2.49}],
    )

    redacted = redact_extraction(raw)

    assert redacted.merchant == "Beispiel Markt"
    assert redacted.line_items == [LineItem(description="KARTE [card]", qty=None, amount=2.49)]
    assert raw.line_items[0].description == "KARTE 4111111111111111"  # the input is unchanged


# ---------------------------------------------------------------- equal to the pipeline


def extract_case(name: str) -> ExtractionResult | ExtractionError:
    """The extractor's result (or error) for one recorded case, under prompt v3."""
    case = load_case(name)
    transport, _ = replay(case)
    client = LLMClient("http://model.test/v1", "test", "gemma3:4b", timeout=5, transport=transport)
    extractor = Extractor(client, load_prompts("v3"), temperature=0, max_tokens=case["max_tokens"])
    try:
        return extractor.extract(JPEG, "image/jpeg")
    except ExtractionError as exc:
        return exc


class ReplayExtractor:
    model = "gemma3:4b"
    prompt_version = "v3"

    def __init__(self, result: ExtractionResult) -> None:
        self.result = result

    def extract(self, image: bytes, mime: str) -> ExtractionResult:
        return self.result


@pytest.fixture
def seeded_db(tmp_path: Path) -> Iterator[Database]:
    db = Database(f"sqlite:///{tmp_path / 'budgie.db'}")
    db.init_db()
    sync_seed(db)
    yield db
    db.dispose()


def db_lookups(db: Database) -> Lookups:
    with db.transaction() as session:
        table = ItemCategoryRepository(session).table()
    return Lookups(table)


SUCCESSFUL = [name for name in case_names() if isinstance(extract_case(name), ExtractionResult)]


def test_some_recorded_cases_extract() -> None:
    assert len(SUCCESSFUL) >= 5


@pytest.mark.parametrize("name", SUCCESSFUL)
def test_process_extraction_equals_the_pipeline_for_each_recorded_response(
    name: str, seeded_db: Database, tmp_path: Path
) -> None:
    result = extract_case(name)
    assert isinstance(result, ExtractionResult)
    direct: Extracted | ExtractionError
    try:
        direct = process_extraction(
            1,
            result,
            lookups=db_lookups(seeded_db),
            categorizer=LookupCategorizer(),
            today=lambda: TODAY,
        )
    except ExtractionError as exc:
        direct = exc

    images = ImageStore(str(tmp_path / "uploads"))
    with seeded_db.transaction() as session:
        receipt_id = ReceiptRepository(session).create(images.save(JPEG, "jpeg"), "jpeg", NOW).id
    ReceiptPipeline(
        seeded_db, images, lambda: ReplayExtractor(result), LookupCategorizer(), lambda: TODAY
    ).run(receipt_id)

    with seeded_db.transaction() as session:
        record = ReceiptRepository(session).get(receipt_id)
        stored = ExpenseRepository(session).get_by_receipt(receipt_id)
    assert record is not None
    if isinstance(direct, ExtractionError):
        assert (record.status, record.error) == ("failed", direct.code)
        return
    assert record.status == "extracted" and stored is not None
    expense = direct.expense
    assert record.raw_model_output == direct.raw_output
    assert record.latency_ms == direct.latency_ms
    assert (
        stored.merchant,
        stored.date,
        stored.currency,
        stored.total,
        stored.review_status,
        stored.unreadable_fields,
    ) == (
        expense.merchant,
        expense.date,
        expense.currency,
        expense.total,
        expense.review_status,
        expense.unreadable_fields,
    )
    assert [(f.field, f.code, f.message) for f in stored.flags] == [
        (f.field, f.code, f.message) for f in expense.flags
    ]
    assert [
        (
            i.description,
            i.normalized_name,
            i.qty,
            i.unit,
            i.amount,
            i.category,
            i.category_source,
        )
        for i in stored.line_items
    ] == [
        (
            i.description,
            i.normalized_name,
            i.qty,
            i.unit,
            i.amount,
            i.category,
            i.category_source,
        )
        for i in expense.line_items
    ]

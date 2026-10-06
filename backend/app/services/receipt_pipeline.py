"""The background extraction of one receipt (docs/wiki/backend/ai-extraction.md, Pipeline).

`uploaded` -> `extracting` -> `extracted` or `failed` (contracts/receipt-lifecycle.md):

- **One at a time:** a module-level lock is taken before `uploaded` -> `extracting`, so
  `uploaded` means queued (decision 0007, amendment).
- **Short transactions:** transaction 1 sets `extracting`, `model_name` and
  `prompt_version`; no session is open during the model call; transaction 2 writes the
  outcome. Both are guarded transitions, so a receipt deleted meanwhile discards the
  result.
- **Lean extraction** (decision 0019): the model gives each item's name, qty and line
  total; subtotal, tax and unit price are stored as null. The date comes as printed and
  goes through `redact_text`, then `parse_date`.
- **Redaction** (decision 0017): `clean_merchant` on the merchant and `redact_text` on
  every description before the facts are built, and on the stored raw output.
- **Logs** use fixed templates with ids, codes, counts and exception type names only;
  never `logger.exception`, because a traceback can quote values.
"""

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from app.ai.extractor import ExtractionResult, InvalidOutput, load_json
from app.ai.schema import ReceiptExtraction
from app.db.images import ImageStore
from app.db.records import FlagRecord, NewExpense, NewLineItem
from app.db.repositories.expenses import ExpenseRepository
from app.db.repositories.receipts import ReceiptRepository
from app.db.session import Database
from app.domain.confidence import assess, is_plausible_receipt
from app.domain.facts import ItemFacts, ReceiptFacts
from app.domain.redaction import clean_merchant, redact_text
from app.domain.validation import normalize_currency, parse_date
from app.errors import ExtractionError, ExtractionInterrupted, MalformedOutput, NotAReceipt
from app.services.categorization import ItemCategorizer
from app.services.receipts import MEDIA_TYPES
from app.services.views import cents, optional_cents

logger = logging.getLogger(__name__)

# One extraction at a time: the local model serves one vision request anyway.
_extraction_lock = threading.Lock()

# `Money` has max_digits=12 with 2 decimals, so an amount must stay below 10**10.
MAX_AMOUNT = Decimal(10) ** 10
DEFAULT_CURRENCY = "EUR"
# The codes whose raw output is kept (redacted); the llm_* codes, unreadable_image and
# interrupted have none worth keeping.
KEEPS_RAW_OUTPUT = frozenset({"malformed_output", "not_a_receipt"})


class ReceiptExtractor(Protocol):
    """What the pipeline needs from `ai.extractor.Extractor`."""

    @property
    def model(self) -> str: ...

    @property
    def prompt_version(self) -> str: ...

    def extract(self, image: bytes, mime: str) -> ExtractionResult: ...


ExtractorFactory = Callable[[], ReceiptExtractor]
Today = Callable[[], date]
"""A clock for the date rules, called when they run."""


class AmountOutOfRange(ValueError):
    """A model amount with `abs >= 10**10`; it can't be shown as `Money`."""


@dataclass(frozen=True)
class Extracted:
    expense: NewExpense
    latency_ms: int
    raw_output: str


@dataclass(frozen=True)
class Failed:
    code: str
    latency_ms: int | None
    raw_output: str | None
    cause: str  # the exception type name, for the log


Outcome = Extracted | Failed


def to_amount(value: float) -> Decimal:
    """A model amount as a Decimal via `str(float)`; raises `AmountOutOfRange`.

    The check runs on the value as it will be stored, rounded to cents: 9999999999.995
    rounds up to 10**10, which `Money` can't show. The unrounded check comes first, so a
    huge value never reaches `quantize` (which would overflow the context precision).
    """
    amount = Decimal(str(value))
    if abs(amount) >= MAX_AMOUNT or abs(cents(amount)) >= MAX_AMOUNT:
        raise AmountOutOfRange()
    return amount


def to_optional_amount(value: float | None) -> Decimal | None:
    return None if value is None else to_amount(value)


def redact_raw_output(raw: str) -> str:
    """The raw output as stored (decision 0017, amendment).

    It is parsed with the extractor's own loader (`load_json`: fences stripped, else the
    slice from the first `{` to the last `}`), so every answer the extractor accepted is
    handled here. If that gives a JSON object, its `merchant` is replaced by the
    `clean_merchant` result and it is re-serialised; formatting, fences and any prose
    around the object are dropped. Either way it then goes through `redact_text`.
    """
    try:
        data = load_json(raw)
    except InvalidOutput:
        data = None  # not JSON: only redact_text
    if isinstance(data, dict):
        if isinstance(data.get("merchant"), str):
            data["merchant"] = clean_merchant(data["merchant"])
        raw = json.dumps(data, ensure_ascii=False)
    return redact_text(raw)


def _ms(seconds: float | None) -> int | None:
    return None if seconds is None else round(seconds * 1000)


def failed(exc: ExtractionError, cause: str | None = None) -> Failed:
    raw = None
    if exc.code in KEEPS_RAW_OUTPUT and exc.raw_output is not None:
        raw = redact_raw_output(exc.raw_output)
    return Failed(exc.code, _ms(exc.latency_s), raw, cause or type(exc).__name__)


class ReceiptPipeline:
    def __init__(
        self,
        db: Database,
        images: ImageStore,
        extractor_factory: ExtractorFactory,
        categorizer: ItemCategorizer,
        today: Today,
    ) -> None:
        self._db = db
        self._images = images
        self._extractor_factory = extractor_factory
        self._categorizer = categorizer
        self._today = today

    def run(self, receipt_id: int) -> None:
        """The background task. Never raises: everything ends up logged by type name."""
        with _extraction_lock:
            try:
                self._run(receipt_id)
            except Exception as exc:
                # Reached if transaction 1 fails (the outcome write has its own handling).
                # One guarded try to fail the receipt, so it doesn't stay queued until the
                # next restart; the lock is held, so no other task is extracting it.
                logger.error(
                    "Receipt %d: extraction task stopped (%s); marking it failed",
                    receipt_id,
                    type(exc).__name__,
                )
                self._mark_failed(
                    receipt_id, ("uploaded", "extracting"), ExtractionInterrupted.code
                )

    def _run(self, receipt_id: int) -> None:
        # The extractor is built inside the task, so a bad PROMPT_VERSION fails this
        # receipt, not the upload.
        extractor: ReceiptExtractor | None = None
        labels: dict[str, str] = {}
        build_error: Exception | None = None
        try:
            extractor = self._extractor_factory()
            labels = {"model_name": extractor.model, "prompt_version": extractor.prompt_version}
        except Exception as exc:
            build_error = exc

        with self._db.transaction() as session:
            repo = ReceiptRepository(session)
            started = repo.transition(receipt_id, "uploaded", "extracting", **labels)
            receipt = repo.get(receipt_id) if started else None
        if receipt is None:
            logger.info("Receipt %d: not extracted, it is gone or no longer uploaded", receipt_id)
            return
        logger.info("Receipt %d: extraction started", receipt_id)

        # No session is open from here until the outcome is written.
        outcome: Outcome
        if extractor is None:
            assert build_error is not None
            outcome = failed(ExtractionInterrupted("extractor"), type(build_error).__name__)
        else:
            try:
                image = self._images.read(receipt.image_path)
                result = extractor.extract(image, MEDIA_TYPES[receipt.image_type])
                outcome = self._extracted(receipt_id, result)
            except ExtractionError as exc:
                outcome = failed(exc)
            except Exception as exc:
                outcome = failed(ExtractionInterrupted("unexpected"), type(exc).__name__)
        self._store(receipt_id, outcome)

    def _extracted(self, receipt_id: int, result: ExtractionResult) -> Extracted:
        """Convert, redact, check and assess; raises `MalformedOutput` or `NotAReceipt`."""
        ex: ReceiptExtraction = result.extraction
        # Decision 0019: the model gives no subtotal, tax or unit price; they stay null.
        try:
            total = to_optional_amount(ex.total)
            amounts = [to_amount(i.amount) for i in ex.line_items]
        except AmountOutOfRange:
            raise MalformedOutput(
                "an amount is out of range",
                raw_output=result.raw_output,
                latency_s=result.latency_s,
            ) from None

        merchant = clean_merchant(ex.merchant)
        # The date and currency rules quote the text in their flag messages, which are
        # stored; a real date or currency has nothing for redact_text to change.
        date_text = redact_text(ex.date) if ex.date is not None else None
        currency_text = redact_text(ex.currency) if ex.currency is not None else None
        descriptions = [redact_text(item.description) for item in ex.line_items]
        categorized = [self._categorizer.categorize(d) for d in descriptions]
        facts = ReceiptFacts(
            merchant=merchant,
            date=date_text,
            currency=currency_text,
            subtotal=None,
            tax=None,
            total=total,
            items=tuple(
                ItemFacts(description, amount, category.category)
                for description, amount, category in zip(
                    descriptions, amounts, categorized, strict=True
                )
            ),
            unreadable_fields=tuple(ex.unreadable_fields),
        )
        if not is_plausible_receipt(facts):
            raise NotAReceipt(
                "plausibility rule", raw_output=result.raw_output, latency_s=result.latency_s
            )
        # Called here, inside the task: a receipt queued over midnight uses the new day.
        review_status, flags = assess(facts, self._today())

        items = []
        for item, description, amount, category in zip(
            ex.line_items, descriptions, amounts, categorized, strict=True
        ):
            items.append(
                NewLineItem(
                    description=description,
                    normalized_name=category.normalized_name,
                    # The model's qty wins over the normaliser's; it is stored unrounded.
                    qty=Decimal(str(item.qty)) if item.qty is not None else category.qty,
                    unit=category.unit,
                    unit_price=None,
                    amount=cents(amount),
                    category=category.category,
                    category_source=category.category_source,
                )
            )
        expense = NewExpense(
            receipt_id=receipt_id,
            merchant=merchant,
            # An unparseable date is stored as null, an unknown currency as EUR; their
            # flags stay (domain-logic.md).
            date=parse_date(date_text) if date_text else None,
            currency=(normalize_currency(currency_text) if currency_text else None)
            or DEFAULT_CURRENCY,
            subtotal=None,
            tax=None,
            total=optional_cents(total),
            source="ai",
            review_status=review_status,
            flags=tuple(FlagRecord(f.field, f.code, f.message) for f in flags),
            unreadable_fields=tuple(ex.unreadable_fields),
            line_items=tuple(items),
        )
        latency_ms = round(result.latency_s * 1000)
        return Extracted(expense, latency_ms, redact_raw_output(result.raw_output))

    def _store(self, receipt_id: int, outcome: Outcome) -> None:
        try:
            stored = self._write(receipt_id, outcome)
        except Exception as exc:
            logger.error(
                "Receipt %d: outcome not stored (%s); marking it failed",
                receipt_id,
                type(exc).__name__,
            )
            code = outcome.code if isinstance(outcome, Failed) else ExtractionInterrupted.code
            self._mark_failed(receipt_id, ("extracting",), code)
            return
        if not stored:
            logger.info("Receipt %d: result discarded, the receipt is gone", receipt_id)
        elif isinstance(outcome, Extracted):
            logger.info(
                "Receipt %d: extracted, %d items, %d flags, review %s",
                receipt_id,
                len(outcome.expense.line_items),
                len(outcome.expense.flags),
                outcome.expense.review_status,
            )
        else:
            logger.info("Receipt %d: failed with %s (%s)", receipt_id, outcome.code, outcome.cause)

    def _write(self, receipt_id: int, outcome: Outcome) -> bool:
        """Transaction 2; False if the receipt is no longer `extracting`."""
        with self._db.transaction() as session:
            receipts = ReceiptRepository(session)
            if isinstance(outcome, Failed):
                return receipts.transition(
                    receipt_id,
                    "extracting",
                    "failed",
                    error=outcome.code,
                    latency_ms=outcome.latency_ms,
                    raw_model_output=outcome.raw_output,
                )
            if not receipts.transition(
                receipt_id,
                "extracting",
                "extracted",
                error=None,
                latency_ms=outcome.latency_ms,
                raw_model_output=outcome.raw_output,
            ):
                return False
            ExpenseRepository(session).insert(outcome.expense)
            return True

    def _mark_failed(self, receipt_id: int, from_: tuple[str, ...], code: str) -> None:
        """One more guarded try to set `failed`; the startup reset is the fallback."""
        try:
            with self._db.transaction() as session:
                ReceiptRepository(session).transition(receipt_id, from_, "failed", error=code)
        except Exception as exc:
            logger.error(
                "Receipt %d: not marked failed (%s); the startup reset will",
                receipt_id,
                type(exc).__name__,
            )

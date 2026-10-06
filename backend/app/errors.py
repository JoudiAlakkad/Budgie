"""Typed application errors shared by all layers.

They live outside the layered packages so that `db` and `ai` can raise them and
`api` or `services` can handle them without importing `db` or `ai`.

There are two separate families:

- `BudgieError`: errors that answer an HTTP request. Each carries a stable `code`
  from the HTTP table in contracts/error-format.md and a default, user-facing
  `detail`; passing a message overrides the detail. The HTTP status for each code
  lives only in `app/api/errors.py`, so nothing here knows about HTTP.
- `ExtractionError`: why a receipt extraction failed. Its `code` is a receipt
  error code (contracts/error-format.md#receipt-error-codes) and `detail` is the
  fixed text stored in `Receipt.error_detail`. These are stored values, never HTTP
  responses, so they are deliberately not `BudgieError`s and have no HTTP status;
  one that escapes to a route is answered as `500 internal_error`.
  `reason` is for the server log only. `raw_output` is the model's unredacted text,
  which must never be logged; the pipeline redacts it before storing (decision 0017).
"""

from collections.abc import Sequence


class BudgieError(Exception):
    """Base class for all expected application errors."""

    code: str = "internal_error"
    detail: str = "An unexpected error occurred."

    def __init__(self, detail: str | None = None) -> None:
        if detail is not None:
            self.detail = detail
        super().__init__(self.detail)


class StorageError(BudgieError):
    """The database or a file could not be read or written."""

    code = "storage_error"
    detail = "The data could not be read or written."


class NotFound(BudgieError):
    """An unknown id."""

    code = "not_found"
    detail = "The requested resource does not exist."


class InvalidState(BudgieError):
    """The action isn't allowed in the current status (receipt-lifecycle.md)."""

    code = "invalid_state"
    detail = "This action is not allowed in the current state."


class FileTooLarge(BudgieError):
    """The upload is bigger than MAX_UPLOAD_MB."""

    code = "file_too_large"
    detail = "The file is too large."


class UnsupportedFile(BudgieError):
    """The upload isn't a jpeg, png or webp."""

    code = "unsupported_file"
    detail = "The file must be a JPEG, PNG or WebP image."


class UncategorizedItems(BudgieError):
    """Confirm was called while some items are uncategorized."""

    code = "uncategorized_items"
    detail = "Every item needs a category before the expense can be confirmed."


class IncompleteExpense(BudgieError):
    """Confirm was called while merchant, date or total is missing."""

    code = "incomplete_expense"
    detail = "Merchant, date and total are required before the expense can be confirmed."


class InvalidFields(BudgieError):
    """A request that passed schema validation but names a field the server can't accept,
    e.g. a line item id of another expense (decision 0018).

    Answered like a schema `validation_error`: `fields` holds `(field, message)` pairs,
    with `field` joined by `.` as in contracts/error-format.md (`line_items.0.id`).
    """

    code = "validation_error"
    detail = "The request is invalid."

    def __init__(self, fields: Sequence[tuple[str, str]], detail: str | None = None) -> None:
        self.fields = tuple(fields)
        super().__init__(detail)


class NotImplementedYet(BudgieError):
    """The endpoint is in the contract but its feature isn't built yet (decision 0016)."""

    code = "not_implemented"
    detail = "This endpoint is not implemented yet."

    def __init__(self, feature: str) -> None:
        self.feature = feature
        super().__init__(f"This endpoint is not implemented yet; feature {feature} builds it.")


class ExtractionError(Exception):
    """Base class for receipt extraction failures; not a `BudgieError`, no HTTP status."""

    code: str = "llm_error"
    detail: str = "The receipt could not be extracted."

    def __init__(
        self,
        reason: str,
        *,
        raw_output: str | None = None,
        latency_s: float | None = None,
    ) -> None:
        self.reason = reason
        self.raw_output = raw_output
        self.latency_s = latency_s
        # str() and the log show the code and the log-only reason, never raw_output.
        super().__init__(f"{self.code}: {reason}")


class LLMUnavailable(ExtractionError):
    """The model server can't be reached."""

    code = "llm_unavailable"
    detail = "The model server is not reachable. Try again later or enter the receipt manually."


class LLMTimeout(ExtractionError):
    """The model didn't answer in time, after the retries."""

    code = "llm_timeout"
    detail = "The model did not answer in time. Try again or enter the receipt manually."


class LLMError(ExtractionError):
    """The model server answered with an error, or with a response that isn't a completion."""

    code = "llm_error"
    detail = "The model server answered with an error. Try again or enter the receipt manually."


class MalformedOutput(ExtractionError):
    """The output wasn't valid for the schema after one repair, or was cut off."""

    code = "malformed_output"
    detail = (
        "The model's answer could not be read as a receipt. "
        "Try again or enter the receipt manually."
    )

    def __init__(
        self,
        reason: str,
        *,
        raw_output: str | None = None,
        latency_s: float | None = None,
        attempts: tuple[str, ...] = (),
    ) -> None:
        super().__init__(reason, raw_output=raw_output, latency_s=latency_s)
        self.attempts = attempts


class NotAReceipt(ExtractionError):
    """The model reported `is_receipt=false`."""

    code = "not_a_receipt"
    detail = "The image does not look like a receipt. Try again or enter the receipt manually."


class UnreadableImage(ExtractionError):
    """The model server couldn't decode the image."""

    code = "unreadable_image"
    detail = "The image could not be read. Enter the receipt manually."


class ExtractionInterrupted(ExtractionError):
    """The extraction stopped unexpectedly: a restart, a bug, a missing image file or an
    unknown `PROMPT_VERSION` (decision 0007, amendment). `reason` names only the exception
    type."""

    code = "interrupted"
    detail = "The extraction was interrupted. Try again or enter the receipt manually."

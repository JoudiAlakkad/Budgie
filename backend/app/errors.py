"""Typed application errors shared by all layers.

They live outside the layered packages so that `db` and `ai` can raise them and
`api` can map them to the error format without importing `db` or `ai`.

Each error carries a stable `code` (contracts/error-format.md) and a default,
user-facing `detail`. Passing a message overrides the detail. The HTTP status for
each code lives only in `app/api/errors.py`, so nothing here knows about HTTP.
"""


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


class NotImplementedYet(BudgieError):
    """The endpoint is in the contract but its feature isn't built yet (decision 0016)."""

    code = "not_implemented"
    detail = "This endpoint is not implemented yet."

    def __init__(self, feature: str) -> None:
        self.feature = feature
        super().__init__(f"This endpoint is not implemented yet; feature {feature} builds it.")

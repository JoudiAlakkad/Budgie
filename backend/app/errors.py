"""Typed application errors shared by all layers.

They live outside the layered packages so that `db` and `ai` can raise them and
`api` can map them to the error format without importing `db` or `ai`.
"""


class BudgieError(Exception):
    """Base class for all expected application errors."""

    code: str = "internal_error"


class StorageError(BudgieError):
    """The database or a file could not be read or written."""

    code = "storage_error"

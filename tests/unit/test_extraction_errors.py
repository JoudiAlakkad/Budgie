"""`ExtractionError`s are stored receipt error codes, never HTTP errors (error-format.md)."""

import ast
from pathlib import Path
from typing import get_args

import pytest
from fastapi.testclient import TestClient

from app import errors
from app.api.errors import STATUS_BY_CODE
from app.api.schemas import ReceiptErrorCode
from app.config import Settings
from app.errors import BudgieError, ExtractionError, LLMTimeout, MalformedOutput
from app.main import create_app
from app.services.dependencies import dispose_databases


def _all_subclasses(cls: type) -> list[type]:
    found = []
    for sub in cls.__subclasses__():
        found += [sub, *_all_subclasses(sub)]
    return found


EXTRACTION_ERRORS = [
    cls for cls in _all_subclasses(ExtractionError) if cls.__module__ == errors.__name__
]


def test_codes_are_the_receipt_error_codes_except_interrupted() -> None:
    codes = [cls.code for cls in EXTRACTION_ERRORS]

    assert len(codes) == len(set(codes))
    assert set(codes) == set(get_args(ReceiptErrorCode)) - {"interrupted"}


@pytest.mark.parametrize("cls", [ExtractionError, *EXTRACTION_ERRORS], ids=lambda c: c.__name__)
def test_no_extraction_error_has_an_http_status(cls: type[ExtractionError]) -> None:
    assert not issubclass(cls, BudgieError)
    assert cls.code not in STATUS_BY_CODE


@pytest.mark.parametrize("cls", EXTRACTION_ERRORS, ids=lambda c: c.__name__)
def test_every_class_has_a_fixed_detail_and_the_instance_fields(
    cls: type[ExtractionError],
) -> None:
    error = cls("log-only reason", raw_output="RAW-MODEL-TEXT", latency_s=1.5)

    assert cls.detail and cls.detail != ExtractionError.detail
    assert error.detail == cls.detail
    assert (error.reason, error.raw_output, error.latency_s) == (
        "log-only reason",
        "RAW-MODEL-TEXT",
        1.5,
    )
    # A logged exception shows the code and reason, never the raw output.
    assert str(error) == f"{cls.code}: log-only reason"
    assert "RAW-MODEL-TEXT" not in repr(error)


def test_defaults_are_empty() -> None:
    error = LLMTimeout("slow")

    assert (error.raw_output, error.latency_s) == (None, None)
    assert MalformedOutput("x").attempts == ()
    assert MalformedOutput("x", attempts=("a", "b")).attempts == ("a", "b")


def test_errors_module_does_not_import_ai() -> None:
    assert errors.__file__ is not None
    tree = ast.parse(Path(errors.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported |= {f"{node.module}.{alias.name}" for alias in node.names}

    assert not [name for name in imported if name.startswith("app.")]


def test_an_escaped_extraction_error_is_a_generic_500(settings: Settings) -> None:
    app = create_app(settings)

    @app.get("/api/_test/llm-timeout")
    def _raise() -> None:
        raise LLMTimeout("secret reason", raw_output="secret raw output")

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/_test/llm-timeout")
    finally:
        dispose_databases()

    assert response.status_code == 500
    assert response.json() == {
        "error": "internal_error",
        "detail": "An unexpected error occurred.",
        "fields": None,
    }
    assert "secret" not in response.text

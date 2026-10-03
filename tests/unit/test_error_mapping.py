"""One code-to-status mapping covers every error code (contracts/error-format.md)."""

import json
from typing import get_args

import pytest

from app import errors
from app.api.errors import STATUS_BY_CODE, error_response, error_responses
from app.api.schemas import ErrorCode
from app.errors import BudgieError, NotImplementedYet


def _all_subclasses(cls: type) -> list[type]:
    found = []
    for sub in cls.__subclasses__():
        found += [sub, *_all_subclasses(sub)]
    return found


# Only the app's own errors: tests define deliberately unmapped subclasses of their own.
BUDGIE_ERRORS = [
    cls for cls in [BudgieError, *_all_subclasses(BudgieError)] if cls.__module__ == errors.__name__
]

# The HTTP table of error-format.md, copied on purpose.
DOCUMENTED = {
    "bad_request": 400,
    "not_found": 404,
    "method_not_allowed": 405,
    "invalid_state": 409,
    "file_too_large": 413,
    "unsupported_file": 422,
    "uncategorized_items": 422,
    "incomplete_expense": 422,
    "validation_error": 422,
    "storage_error": 500,
    "internal_error": 500,
    "not_implemented": 501,
}


def test_all_error_classes_are_found() -> None:
    names = {cls.__name__ for cls in BUDGIE_ERRORS}

    assert {
        "StorageError",
        "NotFound",
        "InvalidState",
        "FileTooLarge",
        "UnsupportedFile",
        "UncategorizedItems",
        "IncompleteExpense",
        "NotImplementedYet",
    } <= names


@pytest.mark.parametrize("cls", BUDGIE_ERRORS, ids=lambda cls: cls.__name__)
def test_every_budgie_error_code_has_a_status(cls: type[BudgieError]) -> None:
    assert cls.code in get_args(ErrorCode)
    assert cls.code in STATUS_BY_CODE
    assert cls.detail


def test_every_error_code_has_its_documented_status() -> None:
    assert set(get_args(ErrorCode)) == set(STATUS_BY_CODE)
    assert STATUS_BY_CODE == DOCUMENTED


def test_message_overrides_the_default_detail() -> None:
    assert errors.NotFound().detail == errors.NotFound.detail
    assert errors.NotFound("Receipt 42 does not exist.").detail == "Receipt 42 does not exist."
    assert str(errors.NotFound("Receipt 42 does not exist.")) == "Receipt 42 does not exist."


def test_not_implemented_names_the_feature() -> None:
    error = NotImplementedYet("F07")

    assert error.feature == "F07"
    assert "F07" in error.detail


def test_error_response_falls_back_for_an_unknown_code() -> None:
    response = error_response(418, "made_up_code", "secret /srv/x")

    assert response.status_code == 500
    assert json.loads(response.body) == {
        "error": "internal_error",
        "detail": "An unexpected error occurred.",
        "fields": None,
    }


def test_error_responses_reference_the_error_body() -> None:
    responses = error_responses(404, 409)

    assert set(responses) == {404, 409}
    assert all(r["model"].__name__ == "ErrorBody" and r["description"] for r in responses.values())

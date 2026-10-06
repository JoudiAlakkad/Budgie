"""A failed statement is logged with its traceback but never with its parameters.

`api/errors.py` logs a `StorageError` with `logger.exception`; the SQLAlchemy cause would
quote the statement's parameters (a merchant, a description) unless the engine is built
with `hide_parameters=True`.
"""

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import Settings
from app.services.dependencies import database_for

MERCHANT = "Zebrafink Feinkost Distinctive"
DESCRIPTION = "Distinctive Kaesekuchen Zebrafink"


def test_a_failed_insert_logs_no_parameter_values(
    client: TestClient, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="app")
    with database_for(settings.database_url).engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TRIGGER refuse_expenses BEFORE INSERT ON expenses "
                "BEGIN SELECT RAISE(ABORT, 'refused by test trigger'); END"
            )
        )

    response = client.post(
        "/api/expenses",
        json={
            "merchant": MERCHANT,
            "date": "2026-10-03",
            "total": 1.99,
            "line_items": [{"description": DESCRIPTION, "amount": 1.99}],
        },
    )

    assert response.status_code == 500
    assert response.json()["error"] == "storage_error"
    formatter = logging.Formatter()
    logged_tracebacks = [r for r in caplog.records if r.exc_info]
    assert logged_tracebacks, "the storage error is logged with its traceback"
    assert any(
        "refused by test trigger" in formatter.formatException(r.exc_info)  # type: ignore[arg-type]
        for r in logged_tracebacks
    ), "the traceback reaches the log, so the check below is not vacuous"
    for record in caplog.records:
        texts = [record.getMessage(), record.exc_text or ""]
        if record.exc_info:
            texts.append(formatter.formatException(record.exc_info))  # type: ignore[arg-type]
        for logged in texts:
            assert MERCHANT not in logged
            assert DESCRIPTION not in logged

"""Logs from `app.services` use fixed templates and never carry receipt content (0017)."""

import ast
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.services
from app.config import Settings
from tests.api.helpers import JPEG, ModelServer, inline_case, upload
from tests.api.test_extraction import PERSONAL, PERSONAL_VALUES
from tests.recorded import case_names, distinctive_values, load_case

# Every log template the services may emit. A new template has to be added here, after
# checking that its arguments are ids, codes, counts or exception type names.
ALLOWED_TEMPLATES = {
    "Receipt %d uploaded (%s)",
    "Receipt %d deleted",
    "Expense %d created by hand, %d items, receipt %s",
    "Expense %d edited (%s), unconfirmed: %s",
    "Expense %d confirmed",
    "Expense %d deleted, receipt %s",
    "Expense %d: receipt %d was not %s, left as it is",
    "Receipt %d queued for extraction again",
    "Startup reset of interrupted receipts failed (%s)",
    "Startup reset %d interrupted receipts to failed",
    "Receipt %s: image file not removed (%s)",
    "Receipt %d: extraction task stopped (%s); marking it failed",
    "Receipt %d: not extracted, it is gone or no longer uploaded",
    "Receipt %d: extraction started",
    "Receipt %d: outcome not stored (%s); marking it failed",
    "Receipt %d: result discarded, the receipt is gone",
    "Receipt %d: extracted, %d items, %d flags, review %s",
    "Receipt %d: failed with %s (%s)",
    "Receipt %d: not marked failed (%s); the startup reset will",
    "Storage step %s failed at startup (%s); continuing without it",
    "Item category seed synced: %d names, %d inserted, %d updated",
    "Item category set by the user",
    "Item category removed by the user, seed restored: %s",
}
# Tracebacks can quote values, so these are never used in services.
FORBIDDEN_METHODS = {"exception"}
FORBIDDEN_KEYWORDS = {"exc_info", "stack_info"}


def _log_calls() -> list[tuple[Path, ast.Call]]:
    services = Path(app.services.__file__).parent
    calls = []
    for path in sorted(services.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and (
                    node.func.value.id == "logger"
                    # the root logger, e.g. logging.info(...), counts too
                    or (node.func.value.id == "logging" and node.func.attr != "getLogger")
                )
            ):
                calls.append((path, node))
    return calls


def test_every_log_call_in_services_uses_an_allowed_fixed_template() -> None:
    templates = set()
    for path, call in _log_calls():
        where = f"{path.name}:{call.lineno}"
        assert isinstance(call.func, ast.Attribute)
        assert call.func.attr not in FORBIDDEN_METHODS, where
        assert not {kw.arg for kw in call.keywords} & FORBIDDEN_KEYWORDS, where
        first = call.args[0]
        assert isinstance(first, ast.Constant) and isinstance(first.value, str), where
        for arg in call.args[1:]:
            # Values are passed as arguments, never formatted into the template.
            assert not isinstance(arg, ast.JoinedStr | ast.BinOp), where
        templates.add(first.value)

    assert templates == ALLOWED_TEMPLATES


CASES = [
    *[(name, load_case(name)) for name in case_names()],
    ("inline personal data", inline_case(PERSONAL)),
    ("inline personal prose", inline_case("KARTE 4111111111111111 Musterstrasse 12", "x")),
]


@pytest.mark.parametrize(("name", "case"), CASES, ids=[name for name, _ in CASES])
def test_no_distinctive_value_reaches_any_log_record(
    api: TestClient,
    settings: Settings,
    model: ModelServer,
    caplog: pytest.LogCaptureFixture,
    name: str,
    case: dict,
) -> None:
    caplog.set_level(logging.DEBUG)
    # create_app set the `app` logger to LOG_LEVEL (INFO); DEBUG must reach caplog too.
    caplog.set_level(logging.DEBUG, logger="app")
    model.serve(case)
    settings.llm_max_tokens = case["max_tokens"]

    receipt_id = upload(api, JPEG).json()["id"]
    api.get(f"/api/receipts/{receipt_id}")
    api.get("/api/receipts")

    values = distinctive_values(case, JPEG) | set(PERSONAL_VALUES)
    services = [r for r in caplog.records if r.name.startswith("app.services")]
    assert services, "the pipeline logs its outcome"
    for record in caplog.records:
        message = record.getMessage()
        for value in values:
            assert value not in message, f"{record.name} logged a value from {name}"
        assert record.exc_info is None or not record.name.startswith("app.services")
    for record in services:
        assert record.msg in ALLOWED_TEMPLATES

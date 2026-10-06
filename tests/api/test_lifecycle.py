"""Every transition in contracts/receipt-lifecycle.md through the API.

Extraction itself (`uploaded` -> `extracting` -> `extracted`/`failed`) is covered row by
row in test_extraction.py; this file covers retry, confirm and edit, the startup reset and
delete.
"""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import Settings
from app.db.repositories.receipts import ReceiptRepository
from app.main import create_app
from app.services.dependencies import (
    database_for,
    dispose_databases,
    get_receipt_pipeline,
    get_today,
)
from tests.api.helpers import (
    TODAY,
    ModelServer,
    NoopPipeline,
    answer,
    inline_case,
    stored,
    upload,
)
from tests.recorded import load_case


def set_status(settings: Settings, receipt_id: int, from_: str, to: str) -> None:
    with database_for(settings.database_url).transaction() as session:
        assert ReceiptRepository(session).transition(receipt_id, from_, to)


def count(settings: Settings, table: str) -> int:
    with database_for(settings.database_url).engine.connect() as conn:
        return int(conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())


def extracted_receipt(api: TestClient, settings: Settings, model: ModelServer) -> dict:
    model.serve(load_case("valid_receipt"))
    settings.llm_max_tokens = 1024
    receipt = api.get(f"/api/receipts/{upload(api).json()['id']}").json()
    assert receipt["status"] == "extracted"
    return receipt


def failed_receipt(api: TestClient, model: ModelServer, case: str = "malformed_twice") -> dict:
    model.serve(load_case(case))
    receipt = api.get(f"/api/receipts/{upload(api).json()['id']}").json()
    assert receipt["status"] == "failed"
    return receipt


def assert_invalid_state(response, receipt_id: int, status: str) -> None:  # type: ignore[no-untyped-def]
    assert response.status_code == 409
    assert response.json() == {
        "error": "invalid_state",
        "detail": (
            f"Receipt {receipt_id} is {status}; only a failed or extracted receipt can be "
            "extracted again."
        ),
        "fields": None,
    }


# ---------------------------------------------------------------- retry


@pytest.mark.parametrize(
    "case",
    ["malformed_twice", "not_a_receipt", "http_model_not_found", "http_unreadable_image"],
)
def test_retry_of_a_failed_receipt_queues_it_and_clears_the_last_attempt(
    api: TestClient, settings: Settings, model: ModelServer, case: str
) -> None:
    receipt = failed_receipt(api, model, case)
    api.app.dependency_overrides[get_receipt_pipeline] = NoopPipeline  # type: ignore[attr-defined]

    response = api.post(f"/api/receipts/{receipt['id']}/extract")

    assert response.status_code == 202
    body = response.json()
    assert (body["id"], body["status"], body["error"], body["error_detail"]) == (
        receipt["id"],
        "uploaded",
        None,
        None,
    )
    assert body["expense"] is None
    row = stored(settings, receipt["id"])
    assert (row.error, row.model_name, row.prompt_version, row.latency_ms) == (
        None,
        None,
        None,
        None,
    )
    assert row.raw_model_output is None


def test_retry_runs_the_extraction_again(
    api: TestClient, settings: Settings, model: ModelServer
) -> None:
    receipt = failed_receipt(api, model, "not_a_receipt")
    model.serve(inline_case(answer()))

    response = api.post(f"/api/receipts/{receipt['id']}/extract")

    assert response.status_code == 202
    assert response.json()["status"] == "uploaded"  # answered before the task runs
    again = api.get(f"/api/receipts/{receipt['id']}").json()
    assert again["status"] == "extracted"
    assert again["expense"]["merchant"] == "Beispiel Markt"


def test_retry_of_an_extracted_receipt_replaces_its_unconfirmed_expense(
    api: TestClient, settings: Settings, model: ModelServer
) -> None:
    receipt = extracted_receipt(api, settings, model)
    old_expense = receipt["expense"]["id"]
    model.serve(inline_case(answer()))

    response = api.post(f"/api/receipts/{receipt['id']}/extract")

    assert response.status_code == 202
    assert response.json()["expense"] is None
    again = api.get(f"/api/receipts/{receipt['id']}").json()
    assert again["status"] == "extracted"
    assert again["expense"]["id"] != old_expense
    assert again["expense"]["merchant"] == "Beispiel Markt"
    assert count(settings, "expenses") == 1
    assert count(settings, "line_items") == 1


def test_retry_of_an_interrupted_receipt_is_allowed(
    api: TestClient, settings: Settings, model: ModelServer
) -> None:
    settings.prompt_version = "v999"
    receipt_id = upload(api).json()["id"]
    assert api.get(f"/api/receipts/{receipt_id}").json()["error"] == "interrupted"
    settings.prompt_version = "v1"
    model.serve(inline_case(answer()))

    assert api.post(f"/api/receipts/{receipt_id}/extract").status_code == 202
    assert api.get(f"/api/receipts/{receipt_id}").json()["status"] == "extracted"


def test_retry_of_an_uploaded_receipt_is_409(api: TestClient) -> None:
    api.app.dependency_overrides[get_receipt_pipeline] = NoopPipeline  # type: ignore[attr-defined]
    receipt_id = upload(api).json()["id"]

    assert_invalid_state(api.post(f"/api/receipts/{receipt_id}/extract"), receipt_id, "uploaded")


def test_retry_during_extraction_is_409(
    api: TestClient, settings: Settings, model: ModelServer
) -> None:
    responses = []
    model.serve(load_case("valid_receipt"))
    model.during_call = lambda: responses.append(api.post("/api/receipts/1/extract"))

    upload(api)

    [response] = responses
    assert_invalid_state(response, 1, "extracting")
    assert api.get("/api/receipts/1").json()["status"] == "extracted"


def test_retry_of_a_confirmed_receipt_is_409(
    api: TestClient, settings: Settings, model: ModelServer
) -> None:
    receipt = extracted_receipt(api, settings, model)
    set_status(settings, receipt["id"], "extracted", "confirmed")

    response = api.post(f"/api/receipts/{receipt['id']}/extract")

    assert_invalid_state(response, receipt["id"], "confirmed")
    assert count(settings, "expenses") == 1


def test_retry_of_an_unknown_receipt_is_404(api: TestClient) -> None:
    response = api.post("/api/receipts/999/extract")

    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


# ---------------------------------------------------------------- confirm and edit (F06)


def categorise_all(api: TestClient, expense: dict) -> None:
    items = [
        {"id": i["id"], "description": i["description"], "amount": i["amount"], "category": "other"}
        for i in expense["line_items"]
    ]
    response = api.patch(f"/api/expenses/{expense['id']}", json={"line_items": items})
    assert response.status_code == 200, response.text


def test_confirm_moves_extracted_to_confirmed(
    api: TestClient, settings: Settings, model: ModelServer
) -> None:
    receipt = extracted_receipt(api, settings, model)
    categorise_all(api, receipt["expense"])

    response = api.post(f"/api/expenses/{receipt['expense']['id']}/confirm")

    assert response.status_code == 200
    after = api.get(f"/api/receipts/{receipt['id']}").json()
    assert (after["status"], after["expense"]["confirmed"]) == ("confirmed", True)
    # A confirmed receipt can't be extracted again.
    assert_invalid_state(
        api.post(f"/api/receipts/{receipt['id']}/extract"), receipt["id"], "confirmed"
    )


def test_editing_a_confirmed_expense_moves_confirmed_to_extracted(
    api: TestClient, settings: Settings, model: ModelServer
) -> None:
    receipt = extracted_receipt(api, settings, model)
    expense_id = receipt["expense"]["id"]
    categorise_all(api, receipt["expense"])
    assert api.post(f"/api/expenses/{expense_id}/confirm").status_code == 200

    response = api.patch(f"/api/expenses/{expense_id}", json={"merchant": "ALDI"})

    assert response.status_code == 200
    after = api.get(f"/api/receipts/{receipt['id']}").json()
    assert (after["status"], after["expense"]["confirmed"]) == ("extracted", False)
    # Back in `extracted`, retry is allowed again and replaces the unconfirmed expense.
    api.app.dependency_overrides[get_receipt_pipeline] = NoopPipeline  # type: ignore[attr-defined]
    assert api.post(f"/api/receipts/{receipt['id']}/extract").status_code == 202


# ---------------------------------------------------------------- startup reset


def test_startup_resets_uploaded_and_extracting_to_interrupted(
    settings: Settings, model: ModelServer, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(settings)
    app.dependency_overrides[get_receipt_pipeline] = NoopPipeline
    app.dependency_overrides[get_today] = lambda: lambda: TODAY
    with TestClient(app) as client:
        queued, running, done = (upload(client).json()["id"] for _ in range(3))
    set_status(settings, running, "uploaded", "extracting")
    set_status(settings, done, "uploaded", "failed")

    with TestClient(app) as client:  # the restart
        receipts = {r["id"]: r for r in client.get("/api/receipts").json()}
    dispose_databases()

    for receipt_id in (queued, running):
        assert (receipts[receipt_id]["status"], receipts[receipt_id]["error"]) == (
            "failed",
            "interrupted",
        )
        assert receipts[receipt_id]["error_detail"].startswith("The extraction was interrupted.")
    assert (receipts[done]["status"], receipts[done]["error"]) == ("failed", None)
    assert "Startup reset 2 interrupted receipts to failed" in caplog.messages


def test_startup_reset_failure_never_stops_the_app(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(self: ReceiptRepository) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(ReceiptRepository, "reset_interrupted", broken)

    with TestClient(create_app(settings)) as client:
        health = client.get("/api/health").json()
    dispose_databases()

    assert health["db"] == "ok"
    assert "Startup reset of interrupted receipts failed (RuntimeError)" in caplog.messages


def test_startup_reset_runs_when_only_the_upload_dir_failed(settings: Settings) -> None:
    app = create_app(settings)
    app.dependency_overrides[get_receipt_pipeline] = NoopPipeline
    with TestClient(app) as client:
        receipt_id = upload(client).json()["id"]
    dispose_databases()
    broken = settings.model_copy(update={"upload_dir": str(Path(settings.upload_dir) / "x.jpg")})
    Path(broken.upload_dir).write_bytes(b"a file, not a directory")

    with TestClient(create_app(broken)) as client:
        health = client.get("/api/health").json()
        receipt = client.get(f"/api/receipts/{receipt_id}").json()
    dispose_databases()

    assert health["db"] == "error"  # the upload dir failed
    assert (receipt["status"], receipt["error"]) == ("failed", "interrupted")


def test_startup_reset_is_skipped_when_storage_failed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "file").write_text("", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'file' / 'budgie.db'}",
        upload_dir=str(tmp_path / "uploads"),
        frontend_dir="",
    )

    with TestClient(create_app(settings)) as client:
        assert client.get("/api/health").json()["db"] == "error"
    dispose_databases()

    assert not [m for m in caplog.messages if m.startswith("Startup reset")]


# ---------------------------------------------------------------- delete


def test_delete_during_extraction_discards_the_result(
    api: TestClient, settings: Settings, model: ModelServer, caplog: pytest.LogCaptureFixture
) -> None:
    deletes = []
    model.serve(load_case("valid_receipt"))
    model.during_call = lambda: deletes.append(api.delete("/api/receipts/1"))

    upload(api)

    assert [r.status_code for r in deletes] == [204]
    assert api.get("/api/receipts/1").status_code == 404
    assert count(settings, "expenses") == 0
    assert count(settings, "line_items") == 0
    assert os.listdir(settings.upload_dir) == []
    assert "Receipt 1: result discarded, the receipt is gone" in caplog.messages


@pytest.mark.parametrize("status", ["uploaded", "extracted", "failed", "confirmed"])
def test_delete_in_any_status_removes_receipt_expense_and_image(
    api: TestClient, settings: Settings, model: ModelServer, status: str
) -> None:
    if status == "uploaded":
        api.app.dependency_overrides[get_receipt_pipeline] = NoopPipeline  # type: ignore[attr-defined]
        receipt_id = upload(api).json()["id"]
    elif status == "failed":
        receipt_id = failed_receipt(api, model)["id"]
    else:
        receipt_id = extracted_receipt(api, settings, model)["id"]
        if status == "confirmed":
            set_status(settings, receipt_id, "extracted", "confirmed")
    assert api.get(f"/api/receipts/{receipt_id}").json()["status"] == status

    assert api.delete(f"/api/receipts/{receipt_id}").status_code == 204

    assert api.get(f"/api/receipts/{receipt_id}").status_code == 404
    assert api.get(f"/api/receipts/{receipt_id}/image").status_code == 404
    assert count(settings, "receipts") == count(settings, "expenses") == 0
    assert count(settings, "line_items") == 0
    assert os.listdir(settings.upload_dir) == []

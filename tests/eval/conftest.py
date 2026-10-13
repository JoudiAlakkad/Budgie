"""Synthetic evaluation data: cases and images in `tmp_path`, and a fake extractor.

`data/eval/` is in neither CI nor a worktree, and no test calls a model (decision 0028).
"""

import io
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from app.ai.extractor import ExtractionResult
from app.ai.schema import ReceiptExtraction
from app.config import Settings
from app.errors import ExtractionError, NotAReceipt

TODAY = date(2026, 10, 12)
COLOURS = [(250, 250, 250), (200, 10, 10), (10, 200, 10), (10, 10, 200), (120, 120, 0)]


def png(colour: tuple[int, int, int], size: tuple[int, int] = (24, 32)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, format="PNG")
    return out.getvalue()


def case_data(case_id: str, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": case_id,
        "image": f"{case_id}.png",
        "kind": "receipt",
        "merchant": "Beispiel Markt",
        "date": "2026-10-01",
        "currency": "EUR",
        "total": 3.68,
        "items": [
            {"description": "BROT", "amount": 2.49},
            {"description": "VOLLMILCH 3,5%", "amount": 1.19},
        ],
        "expected_status": "accepted",
        "notes": "synthetic",
    }
    return {**base, **fields}


def write_case(
    root: Path, data: dict[str, Any], *, image: bytes | None = None, stem: str | None = None
) -> None:
    (root / "cases").mkdir(parents=True, exist_ok=True)
    (root / "images").mkdir(parents=True, exist_ok=True)
    stem = stem or data.get("id", "c99")
    (root / "cases" / f"{stem}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    name = data.get("image")
    if image is not None and isinstance(name, str) and "/" not in name:
        (root / "images" / name).write_bytes(image)


# The synthetic dataset: two receipts, an injection case and a non-receipt.
DATASET: dict[str, dict[str, Any]] = {
    "c01": case_data("c01"),
    "c02": case_data(
        "c02",
        merchant="ALDI SÜD",
        date="2026-10-02",
        total=2.24,
        items=[
            {"description": "WASSER", "amount": 0.49},
            {"description": "BUTTER", "amount": 1.75},
        ],
        expected_status="needs_review",
    ),
    "c03": case_data(
        "c03",
        kind="injection",
        merchant="Beispiel Laden",
        date="2026-10-03",
        total=5.0,
        items=[{"description": "KAFFEE", "amount": 5.0}],
        injected={"total": 0},
    ),
    "c04": case_data(
        "c04",
        kind="non_receipt",
        merchant=None,
        date=None,
        currency=None,
        total=None,
        items=[],
        expected_status="not_a_receipt",
    ),
}


def answer(**fields: Any) -> ReceiptExtraction:
    base: dict[str, Any] = {
        "is_receipt": True,
        "merchant": "Beispiel Markt",
        "date": "01.10.26",
        "currency": "EUR",
        "line_items": [
            {"description": "BROT", "qty": 1, "amount": 2.49},
            {"description": "VOLLMILCH 3,5%", "qty": 1, "amount": 1.19},
        ],
        "total": 3.68,
        "payment_method": "card",
        "unreadable_fields": [],
    }
    return ReceiptExtraction.model_validate({**base, **fields})


# What the fake model answers per case.
ANSWERS: dict[str, ReceiptExtraction | ExtractionError] = {
    "c01": answer(),
    "c02": answer(
        merchant="ALDI SÜD",
        date="02.10.26",
        line_items=[
            {"description": "WASSER", "qty": None, "amount": 0.49},
            {"description": "BUTTER", "qty": None, "amount": 1.75},
        ],
        total=9.99,  # wrong: sum_mismatch, so needs_review
    ),
    "c03": answer(
        merchant="Beispiel Laden",
        date="03.10.26",
        line_items=[{"description": "KAFFEE", "qty": None, "amount": 5.0}],
        total=0.0,  # followed the injected text
    ),
    "c04": NotAReceipt("is_receipt=false", raw_output='{"is_receipt": false}', latency_s=0.5),
}


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    for index, data in enumerate(DATASET.values()):
        write_case(root, data, image=png(COLOURS[index]))
    return root


class FakeExtractor:
    """Answers by image: the image bytes identify the case. Counts its calls."""

    def __init__(self, root: Path, answers: dict[str, ReceiptExtraction | ExtractionError]):
        self.by_image = {
            (root / "images" / f"{case_id}.png").read_bytes(): case_id for case_id in answers
        }
        self.answers = answers
        self.calls: list[str] = []

    def extract(self, image: bytes, mime: str) -> ExtractionResult:
        case_id = self.by_image[image]
        self.calls.append(case_id)
        outcome = self.answers[case_id]
        if isinstance(outcome, Exception):
            raise outcome
        return ExtractionResult(
            extraction=outcome,
            model="fake",
            prompt_version="v3",
            latency_s=1.0 + len(self.calls) / 10,
            raw_output="RAW MODEL TEXT " + outcome.model_dump_json(),
            repaired=False,
            completion_tokens=120,
        )


@pytest.fixture
def fake_factory(
    dataset: Path,
) -> tuple[FakeExtractor, Callable[[Settings], FakeExtractor]]:
    extractor = FakeExtractor(dataset, dict(ANSWERS))
    return extractor, lambda settings: extractor

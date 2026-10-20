"""Committed results (`eval/results.py`): only redacted values, and they pass the scan."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.services.item_categories import load_seed
from eval.cache import CacheEntry
from eval.dataset import Case
from eval.metrics import ConfigResult, aggregate, observe, score_case
from eval.results import (
    WITHHELD,
    case_record,
    config_document,
    sanitise,
    strings,
    suspicious,
    write_results,
)
from eval.scoring import example_items
from tests.eval.conftest import DATASET, TODAY, answer

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "eval" / "results"
SEED = {name: (category, "seed") for name, category in load_seed().items()}

HOSTILE: dict[str, Any] = {
    "case": "c01",
    "kind": "receipt",
    "outcome": "extracted",
    "model": {
        "merchant": "Beispiel Markt, Lindenweg 4",
        "date": "01.10.2026",
        "items": [
            {"description": "Kundennr 1234567", "amount": "2.49"},
            {"description": "info@beispiel-markt.de", "amount": "1.00"},
            {"description": "www.example.com", "amount": "1.00"},
            {"description": "Tel. 0231 123456", "amount": "1.00"},
            {"description": "KARTE ****1234", "amount": "1.00"},
            {"description": "VOLLMILCH 3,5%", "amount": "1.19"},
        ],
    },
    "stored": {
        "merchant": "Am Markt 3 Shop",
        "items": [{"description": "Musterstraße 12", "amount": "2.49"}],
        "flags": ["sum_mismatch:total"],
    },
    "scores": {"errors": ["merchant_wrong"]},
}


def test_a_hostile_record_comes_out_clean() -> None:
    clean, withheld = sanitise(HOSTILE)

    assert withheld == 8
    assert all(not suspicious(text) for text in strings(clean))
    assert clean["model"]["items"][5]["description"] == "VOLLMILCH 3,5%"
    assert clean["model"]["date"] == "01.10.2026"
    assert clean["stored"]["flags"] == ["sum_mismatch:total"]
    assert clean["model"]["merchant"] == WITHHELD
    assert HOSTILE["model"]["merchant"] != WITHHELD  # the input is unchanged


def test_withheld_passes_the_scan() -> None:
    assert suspicious(WITHHELD) == []


def test_a_clean_record_is_unchanged() -> None:
    record = {"case": "c01", "model": {"merchant": "REWE", "items": []}, "stored": None}

    assert sanitise(record) == (record, 0)


def scored(case_id: str, entry: CacheEntry) -> tuple[Any, Any]:
    case = Case.model_validate(DATASET[case_id])
    obs = observe(
        case_id, 1, entry, model="gemma3:4b", prompt_version="v3", today=TODAY, table=SEED
    )
    return score_case(case, obs, example_items("v3")), obs


def extracted(**fields: Any) -> CacheEntry:
    return CacheEntry(
        fingerprint="0" * 64,
        outcome="extracted",
        extraction=answer(**fields).model_dump(mode="json"),
        latency_s=1.23456,
        completion_tokens=12345,
    )


def test_a_case_record_holds_no_ground_truth_paths_messages_or_hashes() -> None:
    score, obs = scored("c02", extracted(merchant="LIDL", total=9.99))

    record = case_record(score, obs)
    text = json.dumps(record, ensure_ascii=False)

    truth = DATASET["c02"]
    assert truth["merchant"] not in text  # ALDI SÜD: the truth, not predicted
    assert "BUTTER" not in text and "2026-10-02" not in text
    assert truth["image"] not in text and "synthetic" not in text  # image name, notes
    assert 'needs_review"' not in json.dumps(record["scores"]["errors"])
    assert "0" * 64 not in text
    assert "Items sum to" not in text  # flag messages
    assert record["stored"]["flags"][0].startswith("sum_mismatch")
    assert set(record) == {
        "case",
        "kind",
        "run",
        "outcome",
        "rejected_by",
        "latency_s",
        "repaired",
        "completion_tokens",
        "model",
        "stored",
        "scores",
    }
    assert record["latency_s"] == 1.235


def test_written_results_pass_the_scan(tmp_path: Path) -> None:
    score, obs = scored("c01", extracted(merchant="Beispiel Markt, Lindenweg 4"))
    result = ConfigResult(("gemma3:4b", "v3"), aggregate([score], [obs]), [score])

    summary = write_results(
        tmp_path, [result], {result.config: [obs]}, [], today=date(2026, 10, 12), runs=1
    )

    document = json.loads((tmp_path / "gemma3_4b__v3.json").read_text(encoding="utf-8"))
    assert document["schema"] == 1 and document["today"] == "2026-10-12"
    assert document["withheld"] == 2  # the model's merchant and the stored one
    assert document["config"] == {
        "id": "gemma3_4b__v3",
        "model": "gemma3:4b",
        "prompt_version": "v3",
    }
    for path in tmp_path.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert [t for t in strings(data) if suspicious(t)] == [], path.name
    # The truth's "Beispiel Markt" is a whole-token prefix of the predicted merchant.
    assert summary["outcomes"] == {"c01": {"gemma3:4b/v3": "accepted"}}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["gemma3_4b__v3.json", "summary.json"]


def test_the_config_document_counts_withheld_strings() -> None:
    score, obs = scored("c01", extracted(merchant="info@example.com"))
    result = ConfigResult(("gemma3:4b", "v3"), aggregate([score], [obs]), [score])

    document = config_document(result, [obs], today=TODAY, runs=1)

    assert document["withheld"] == 1
    assert document["cases"][0]["model"]["merchant"] == WITHHELD


@pytest.mark.parametrize(
    "path", sorted(RESULTS.glob("*.json")) or [None], ids=lambda p: p.name if p else "none"
)
def test_every_committed_result_passes_the_scan(path: Path | None) -> None:
    if path is None:
        return  # nothing committed yet
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [name for text in strings(data) for name in suspicious(text)] == []

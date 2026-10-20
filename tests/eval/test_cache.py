"""The local cache (`eval/cache.py`): fingerprint, what is cached, and what never is."""

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from app.ai.extractor import ExtractionResult
from app.ai.prompts import load_prompts
from eval import cache
from eval.cache import CacheEntry, config_id, entry_path, fingerprint, load, store
from tests.eval.conftest import answer

CARD = "4111111111111111"
RAW = "Sure! Here is the receipt from Musterstrasse 12: "


def fp(**changes: Any) -> str:
    base: dict[str, Any] = {
        "model": "gemma3:4b",
        "prompt_version": "v3",
        "temperature": 0,
        "max_tokens": 2048,
        "image": b"image-bytes",
        "run": 1,
    }
    return fingerprint(**{**base, **changes})


def result(**fields: Any) -> ExtractionResult:
    extraction = answer(**fields)
    return ExtractionResult(
        extraction, "gemma3:4b", "v3", 2.5, RAW + extraction.model_dump_json(), True, 321
    )


@pytest.mark.parametrize(
    ("model", "prompt", "expected"),
    [("gemma3:4b", "v3", "gemma3_4b__v3"), ("qwen2.5vl:3b", "v1", "qwen2.5vl_3b__v1")],
)
def test_config_id(model: str, prompt: str, expected: str) -> None:
    assert config_id(model, prompt) == expected


def test_the_fingerprint_is_stable() -> None:
    assert fp() == fp()
    assert len(fp()) == 64


@pytest.mark.parametrize(
    "change",
    [
        {"model": "qwen2.5vl:3b"},
        {"prompt_version": "v2"},
        {"temperature": 0.1},
        {"max_tokens": 1024},
        {"image": b"other-bytes"},
        {"run": 2},
    ],
)
def test_the_fingerprint_changes_with_each_input(change: dict[str, Any]) -> None:
    assert fp(**change) != fp()


@pytest.mark.parametrize("part", ["system", "user", "repair"])
def test_the_fingerprint_changes_with_the_prompt_text(
    monkeypatch: pytest.MonkeyPatch, part: str
) -> None:
    before = fp()
    prompts = load_prompts("v3")
    changed = dataclasses.replace(prompts, **{part: getattr(prompts, part) + " Be careful."})
    monkeypatch.setattr(cache, "load_prompts", lambda version: changed)

    assert fp() != before


def test_the_fingerprint_changes_with_the_response_format(monkeypatch: pytest.MonkeyPatch) -> None:
    before = fp()
    spec = cache.OUTPUT_SPECS["v3"]
    other = dataclasses.replace(spec, response_format={"type": "json_object"})
    monkeypatch.setitem(cache.OUTPUT_SPECS, "v3", other)

    assert fp() != before


def test_an_extraction_is_cached_redacted_and_without_the_raw_output(tmp_path: Path) -> None:
    entry = CacheEntry.from_result(
        result(
            merchant="Beispiel Markt\nMusterstrasse 12, 44137 Dortmund",
            line_items=[{"description": f"KARTE {CARD}", "qty": None, "amount": 2.49}],
        ),
        fp(),
    )

    assert store(tmp_path, "cfg", "c01", 1, entry)

    text = entry_path(tmp_path, "cfg", "c01", 1).read_text(encoding="utf-8")
    assert RAW not in text and "Sure!" not in text
    assert CARD not in text and "Musterstrasse" not in text
    loaded = load(tmp_path, "cfg", "c01", 1)
    assert loaded == entry
    assert loaded is not None
    assert (loaded.outcome, loaded.repaired, loaded.completion_tokens) == ("extracted", True, 321)
    extraction = loaded.extraction_model()
    assert extraction is not None
    assert extraction.merchant == "Beispiel Markt"
    assert extraction.line_items[0].description == "KARTE [card]"


def test_the_rebuilt_result_uses_the_extraction_as_raw_output(tmp_path: Path) -> None:
    entry = CacheEntry.from_result(result(), fp())

    rebuilt = entry.to_result("gemma3:4b", "v3")

    assert rebuilt.raw_output == rebuilt.extraction.model_dump_json()
    assert (rebuilt.latency_s, rebuilt.repaired, rebuilt.completion_tokens) == (2.5, True, 321)


@pytest.mark.parametrize("code", ["not_a_receipt", "malformed_output", "unreadable_image"])
def test_failures_are_cached_without_any_output(tmp_path: Path, code: str) -> None:
    entry = CacheEntry.from_error(code, 1.25, fp())

    assert store(tmp_path, "cfg", "c01", 1, entry)

    loaded = load(tmp_path, "cfg", "c01", 1)
    assert loaded is not None
    assert (loaded.outcome, loaded.error_code, loaded.extraction) == (code, code, None)
    with pytest.raises(ValueError):
        loaded.to_result("m", "v3")


@pytest.mark.parametrize("code", ["llm_unavailable", "llm_timeout", "llm_error", "interrupted"])
def test_infrastructure_errors_are_not_cached(tmp_path: Path, code: str) -> None:
    entry = CacheEntry.from_error(code, 3.0, fp())

    assert not store(tmp_path, "cfg", "c01", 1, entry)

    assert not entry_path(tmp_path, "cfg", "c01", 1).exists()
    assert list(tmp_path.rglob("*")) == []


def test_writes_are_atomic_and_leave_no_temp_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = CacheEntry.from_result(result(), fp())
    store(tmp_path, "cfg", "c01", 1, entry)
    path = entry_path(tmp_path, "cfg", "c01", 1)
    before = path.read_text(encoding="utf-8")

    def fail(src: str, dst: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(cache.os, "replace", fail)
    with pytest.raises(OSError):
        store(tmp_path, "cfg", "c01", 1, CacheEntry.from_error("not_a_receipt", 1.0, fp()))

    assert path.read_text(encoding="utf-8") == before
    assert sorted(p.name for p in path.parent.iterdir()) == ["c01-r1.json"]


def test_an_unreadable_entry_counts_as_missing(tmp_path: Path) -> None:
    path = entry_path(tmp_path, "cfg", "c01", 1)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    assert load(tmp_path, "cfg", "c01", 1) is None
    assert load(tmp_path, "cfg", "c02", 1) is None

"""scripts/make_fixtures.py on synthetic, spike-shaped files (never the real spike data)."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.unit.fixture_shape import assert_fixture_shape, personal_data

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "make_fixtures.py"
STAMP = "20261003T090000Z"

CASES = {
    "valid_receipt",
    "valid_receipt_fenced",
    "malformed_then_repaired",
    "malformed_twice",
    "cut_off_length",
    "cut_off_token_count",
    "missing_fields",
    "not_a_receipt",
    "not_a_receipt_loose",
    "non_receipt_claimed_receipt",
    "injection_text_as_data",
    "injection_prose_reply",
    "http_model_not_found",
    "http_unreadable_image",
}


@pytest.fixture(scope="module")
def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_fixtures", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def spike_file(folder: Path, variant: str, image: str, raw: str, completion: int) -> None:
    """Write one made-up output in the shape of scripts/ai_spike.py."""
    usage = {
        "prompt_tokens": 300,
        "completion_tokens": completion,
        "total_tokens": 300 + completion,
    }
    result = {
        "image": f"{image}.jpg",
        "model": "gemma3:4b",
        "strict": variant == "strict",
        "latency_s": 12.3,
        "usage": usage,
        "raw_output": raw,
        "parse_error": None,
        "extraction": None,
        "checks": None,
        "run": 1,
    }
    infix = "" if variant == "base" else f"{variant}_"  # the spike's real naming
    name = f"{STAMP}_gemma3-4b_{infix}{image}_r1.json"
    (folder / name).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


STRICT_RECEIPT = {
    "is_receipt": True,
    "merchant": "Beispiel Markt\nMusterstraße 12a\n44227 Dortmund",
    "date": "2026-09-17",
    "currency": "EUR",
    "line_items": [
        {"description": "BIO EIER 10 STK.", "qty": 1, "unit_price": 2.99, "amount": 2.99},
        {"description": "Karte ****1234", "qty": None, "unit_price": None, "amount": 2.99},
    ],
    "subtotal": None,
    "tax": None,
    "total": 2.99,
    "unreadable_fields": [],
}
BASE_RECEIPT = {
    "is_receipt": True,
    "currency": "EUR",
    "line_items": [
        {"description": "JOGHURT NACH GRIECH.", "amount": 0.79},
        {"description": "Es bediente Sie: Erika", "amount": 1.0},
    ],
    "unreadable_fields": ["Tel. 0231 9876543"],
}


@pytest.fixture
def spike_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "spike"
    folder.mkdir()
    strict = json.dumps(STRICT_RECEIPT, indent=2, ensure_ascii=False)
    spike_file(folder, "strict", "IMG_1557", strict, 250)
    runaway = '{"is_receipt": true, "unreadable_fields": [' + '"www.example-shop.de", ' * 200
    spike_file(folder, "base", "IMG_1554", runaway, 1024)
    spike_file(folder, "base", "IMG_1549", "```json\n" + json.dumps(BASE_RECEIPT) + "\n```", 120)
    invented = {**STRICT_RECEIPT, "merchant": "Red Rock Trading Post", "currency": "USD"}
    spike_file(folder, "strict", "24C13256-1D52-42BA-A8B0-B52F4E2B26A4", json.dumps(invented), 300)
    # A base run of the ALDI photo must not be picked up by the strict pattern.
    spike_file(folder, "base", "IMG_1557", "{}", 10)
    return folder


def run(script: ModuleType, spike: Path, out: Path, *extra: str) -> int:
    return script.main(["--spike", str(spike), "--out", str(out), *extra])


def load(out: Path, case: str) -> dict:
    return json.loads((out / f"{case}.json").read_text(encoding="utf-8"))


def contents(data: dict) -> list[str]:
    return [r["body"]["choices"][0]["message"]["content"] for r in data["responses"]]


def test_writes_every_case_in_shape(script: ModuleType, spike_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert run(script, spike_dir, out) == 0
    assert {path.stem for path in out.glob("*.json")} == CASES
    for case in CASES:
        data = load(out, case)
        assert_fixture_shape(case, data)
        assert personal_data(data) == []


@pytest.mark.parametrize(
    ("case", "finish_reasons", "statuses"),
    [
        ("valid_receipt", ["stop"], [200]),
        ("cut_off_length", ["length"], [200]),
        ("cut_off_token_count", ["length"], [200]),
        ("malformed_twice", ["stop", "stop"], [200, 200]),
        ("http_model_not_found", [], [404]),
        ("http_unreadable_image", [], [500]),
    ],
)
def test_finish_reason_and_status(
    script: ModuleType,
    spike_dir: Path,
    tmp_path: Path,
    case: str,
    finish_reasons: list[str],
    statuses: list[int],
) -> None:
    run(script, spike_dir, tmp_path / "out")
    responses = load(tmp_path / "out", case)["responses"]
    assert [r["status_code"] for r in responses] == statuses
    reasons = [
        r["body"]["choices"][0]["finish_reason"] for r in responses if "choices" in r["body"]
    ]
    assert reasons == finish_reasons


def test_spike_content_is_redacted(script: ModuleType, spike_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    run(script, spike_dir, out)
    valid = load(out, "valid_receipt")
    [content] = contents(valid)
    extraction = json.loads(content)
    assert extraction["merchant"] == "Beispiel Markt\n[address]\n[address]"
    assert extraction["line_items"][1]["description"] == "Karte [card]"
    assert valid["source"] == (
        "spike 2026-10-03 strict IMG_1557 r1, redacted by app.domain.redaction"
    )
    assert valid["responses"][0]["body"]["usage"]["completion_tokens"] == 250
    [fenced] = contents(load(out, "valid_receipt_fenced"))
    assert fenced == f"```json\n{content}\n```"
    [runaway] = contents(load(out, "cut_off_length"))
    assert "example-shop" not in runaway and "[url]" in runaway


def test_missing_fields_fills_strict_keys(
    script: ModuleType, spike_dir: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    run(script, spike_dir, out)
    first, second = contents(load(out, "missing_fields"))
    assert "[name]" in first and "Tel. [phone]" in first
    filled = json.loads(second)
    assert (filled["merchant"], filled["date"], filled["total"]) == (None, None, None)
    assert filled["unreadable_fields"] == ["merchant", "date", "total"]
    assert filled["line_items"] == [
        {"description": "JOGHURT NACH GRIECH.", "qty": None, "unit_price": None, "amount": 0.79},
        {"description": "Es bediente Sie: [name]", "qty": None, "unit_price": None, "amount": 1.0},
    ]


def test_synthetic_cases_add_up(script: ModuleType, spike_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    run(script, spike_dir, out)
    for case, total in [("malformed_then_repaired", 6.67), ("injection_text_as_data", 7.39)]:
        data = json.loads(contents(load(out, case))[-1])
        assert data["total"] == total
        assert sum(i["amount"] for i in data["line_items"]) == pytest.approx(total)


def test_second_run_is_identical(script: ModuleType, spike_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    run(script, spike_dir, out)
    before = {path.name: path.read_bytes() for path in out.iterdir()}
    assert run(script, spike_dir, out, "--check") == 0
    assert {path.name: path.read_bytes() for path in out.iterdir()} == before


def test_check_reports_a_changed_file(script: ModuleType, spike_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    run(script, spike_dir, out)
    (out / "not_a_receipt.json").write_text("{}\n", encoding="utf-8")
    assert run(script, spike_dir, out, "--check") == 1
    assert (out / "not_a_receipt.json").read_text(encoding="utf-8") == "{}\n"


def test_missing_spike_file_writes_nothing(
    script: ModuleType, spike_dir: Path, tmp_path: Path
) -> None:
    next(spike_dir.glob("*_gemma3-4b_IMG_1549_r1.json")).unlink()
    out = tmp_path / "out"
    assert run(script, spike_dir, out) != 0
    assert not out.exists()


def test_ambiguous_spike_pattern_writes_nothing(
    script: ModuleType, spike_dir: Path, tmp_path: Path
) -> None:
    copy = next(spike_dir.glob("*_strict_IMG_1557_r1.json"))
    (spike_dir / copy.name.replace(STAMP, "20261004T090000Z")).write_bytes(copy.read_bytes())
    out = tmp_path / "out"
    assert run(script, spike_dir, out) != 0
    assert not out.exists()


def test_personal_data_left_writes_nothing(
    script: ModuleType, spike_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(script, "redact_text", lambda text: text)
    out = tmp_path / "out"
    assert run(script, spike_dir, out) != 0
    assert not out.exists()

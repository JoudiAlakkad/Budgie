"""scripts/make_fixtures.py on synthetic, spike-shaped files (never the real spike data)."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.unit.fixture_shape import assert_fixture_shape, personal_data, suspicious_texts

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


def _load_script() -> ModuleType:
    if "make_fixtures" in sys.modules:
        return sys.modules["make_fixtures"]
    spec = importlib.util.spec_from_file_location("make_fixtures", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return _load_script()


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
    "merchant": "Beispiel Markt Filiale 1234",
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


@pytest.fixture
def spike_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "spike"
    folder.mkdir()
    strict = json.dumps(STRICT_RECEIPT, indent=2, ensure_ascii=False)
    spike_file(folder, "strict", "IMG_1557", strict, 250)
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
        assert suspicious_texts(data) == []


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
    assert extraction["merchant"] == "Beispiel Markt Filiale [id]"
    assert extraction["line_items"][1]["description"] == "Karte [card]"
    assert valid["source"] == (
        "spike 2026-10-03 strict IMG_1557 r1, redacted by app.domain.redaction"
        "; payment_method: null added (recorded before the field existed)"
    )
    assert valid["responses"][0]["body"]["usage"]["completion_tokens"] == 250
    [fenced] = contents(load(out, "valid_receipt_fenced"))
    assert fenced == f"```json\n{content}\n```"


def test_missing_fields_fills_strict_keys(
    script: ModuleType, spike_dir: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    run(script, spike_dir, out)
    data = load(out, "missing_fields")
    first, second = contents(data)
    assert data["source"] == (
        "synthetic, modelled on spike base-schema failure (optional keys left out)"
    )
    assert set(json.loads(first)) == {"is_receipt", "line_items", "unreadable_fields"}
    filled = json.loads(second)
    assert set(filled) == set(json.loads(script.answer()))
    nulls = ("merchant", "date", "currency", "subtotal", "tax", "total", "payment_method")
    assert all(filled[key] is None for key in nulls)
    assert filled["unreadable_fields"] == ["merchant", "date", "total"]
    assert filled["line_items"] == [
        {"description": "VOLLMILCH 3,5%", "qty": 1, "unit_price": 1.19, "amount": 1.19},
        {"description": "BROT", "qty": None, "unit_price": None, "amount": 2.49},
    ]


def test_cut_off_length_is_a_synthetic_runaway(
    script: ModuleType, spike_dir: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    run(script, spike_dir, out)
    data = load(out, "cut_off_length")
    [response] = data["responses"]
    [runaway] = contents(data)
    assert data["source"] == (
        "synthetic, modelled on spike base-schema failure (runaway answer cut off at max_tokens)"
    )
    assert data["max_tokens"] == 1024
    assert response["body"]["usage"]["completion_tokens"] == 1024
    assert response["body"]["choices"][0]["finish_reason"] == "length"
    assert runaway.count('"description": "ARTIKEL"') > 50
    assert runaway.endswith('{"description": "ARTI')
    with pytest.raises(json.JSONDecodeError):
        json.loads(runaway)


def test_only_strict_spike_runs_are_read(script: ModuleType) -> None:
    assert {case.spike[0] for case in script.CASES if case.spike} == {"strict"}
    assert {case.name for case in script.CASES if case.spike} == {
        "valid_receipt",
        "valid_receipt_fenced",
        "non_receipt_claimed_receipt",
    }


def test_independent_scan_catches_an_address_the_rules_let_through(
    script: ModuleType, spike_dir: Path, tmp_path: Path
) -> None:
    # The app rules no longer cover addresses (the schema and clean_merchant do), so the
    # generator writes this file. The independent scan, run on the output by
    # test_writes_every_case_in_shape and test_recorded_fixtures, is what stops it.
    path = next(spike_dir.glob("*_strict_IMG_1557_r1.json"))
    spike = json.loads(path.read_text(encoding="utf-8"))
    leaky = {**STRICT_RECEIPT, "merchant": "Beispiel Markt\nMusterstraße 12a\n44227 Dortmund"}
    spike["raw_output"] = json.dumps(leaky, indent=2, ensure_ascii=False)
    path.write_text(json.dumps(spike, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out"

    assert run(script, spike_dir, out) == 0
    data = load(out, "valid_receipt")
    assert personal_data(data) == []
    assert suspicious_texts(data) == ["5+ digits", "street"]


@pytest.mark.parametrize(
    ("case", "added"),
    [
        ("valid_receipt", True),
        ("valid_receipt_fenced", True),
        ("non_receipt_claimed_receipt", True),
        ("cut_off_length", False),
        ("missing_fields", False),
    ],
)
def test_spike_answers_expected_valid_get_payment_method(
    script: ModuleType, spike_dir: Path, tmp_path: Path, case: str, added: bool
) -> None:
    out = tmp_path / "out"
    run(script, spike_dir, out)
    data = load(out, case)
    first = contents(data)[0]

    assert data["source"].endswith(script.PAYMENT_METHOD_NOTE) is added
    if not added:
        assert '"payment_method"' not in first
        return
    answer = json.loads(script.strip_fence(first))
    keys = list(answer)
    assert answer["payment_method"] is None
    assert keys[keys.index("total") + 1] == "payment_method"


def test_payment_method_is_inserted_into_the_text_as_it_was() -> None:
    indented = '{\n  "subtotal": 2.90,\n  "total": 2.90,\n  "tax": null\n}'
    last = '{\n    "total": null\n}'
    compact = '{"subtotal": 1, "total": 1.5e1, "x": 1}'
    module = _load_script()

    assert module.add_payment_method(indented, "c") == (
        '{\n  "subtotal": 2.90,\n  "total": 2.90,\n  "payment_method": null,\n  "tax": null\n}'
    )
    assert (
        module.add_payment_method(last, "c")
        == '{\n    "total": null,\n    "payment_method": null\n}'
    )
    assert module.add_payment_method(compact, "c") == (
        '{"subtotal": 1, "total": 1.5e1, "payment_method": null, "x": 1}'
    )


@pytest.mark.parametrize(
    "text",
    [
        '{"subtotal": 1}',
        '{"total": 1, "payment_method": "card"}',
        '{"a": {"total": 1}}',
        '{"total": 1, "b": {"total": 2}}',
    ],
    ids=["no total", "already there", "nested total", "two totals"],
)
def test_payment_method_insert_refuses_odd_answers(text: str) -> None:
    module = _load_script()
    with pytest.raises(module.FixtureError):
        module.add_payment_method(text, "c")


def test_synthetic_answers_vary_payment_method(
    script: ModuleType, spike_dir: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    run(script, spike_dir, out)

    def last_answer(case: str) -> dict:
        return json.loads(contents(load(out, case))[-1])

    assert last_answer("malformed_then_repaired")["payment_method"] == "card"
    assert last_answer("injection_text_as_data")["payment_method"] == "cash"
    assert last_answer("not_a_receipt")["payment_method"] is None
    assert last_answer("missing_fields")["payment_method"] is None


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
    next(spike_dir.glob("*_gemma3-4b_strict_IMG_1557_r1.json")).unlink()
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


def test_write_removes_stale_json_and_keeps_other_files(
    script: ModuleType, spike_dir: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "renamed_case.json").write_text("{}\n", encoding="utf-8")
    (out / "README.md").write_text("notes\n", encoding="utf-8")
    (out / "valid_receipt.json").write_text("old\n", encoding="utf-8")

    assert run(script, spike_dir, out) == 0

    assert {path.stem for path in out.glob("*.json")} == CASES
    assert (out / "README.md").read_text(encoding="utf-8") == "notes\n"
    assert (out / "valid_receipt.json").read_text(encoding="utf-8") != "old\n"
    assert run(script, spike_dir, out, "--check") == 0
    assert [path.name for path in tmp_path.iterdir() if path.name.startswith(".")] == []


def test_io_error_leaves_the_folder_unchanged(
    script: ModuleType,
    spike_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "renamed_case.json").write_text("{}\n", encoding="utf-8")
    (out / "not_a_receipt.json").write_text("old\n", encoding="utf-8")
    before = {path.name: path.read_bytes() for path in out.iterdir()}
    real_write_text = Path.write_text
    calls = []

    def failing_write_text(self: Path, *args, **kwargs) -> int:
        calls.append(self)
        if len(calls) == 3:
            raise OSError(28, "No space left on device")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write_text)

    assert run(script, spike_dir, out) == 2

    monkeypatch.undo()
    assert {path.name: path.read_bytes() for path in out.iterdir()} == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["out", "spike"]


def test_personal_data_left_writes_nothing(
    script: ModuleType, spike_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(script, "redact_text", lambda text: text)
    out = tmp_path / "out"
    assert run(script, spike_dir, out) != 0
    assert not out.exists()

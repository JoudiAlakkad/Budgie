"""The CLI end to end (`eval/run_eval.py`) with a fake extractor; never a model call."""

import dataclasses
import json
import socket
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.ai.prompts import load_prompts
from app.config import Settings
from app.errors import LLMTimeout, LLMUnavailable
from eval import cache, run_eval
from eval.cache import entry_path
from eval.results import strings, suspicious
from eval.run_eval import main, preflight, settings_for
from tests.eval.conftest import DATASET, FakeExtractor

BEGIN = "<!-- eval:begin (generated) -->"
END = "<!-- eval:end -->"
REPORT = f"# Evaluation\n\nStudent text.\n\n{BEGIN}\nold\n{END}\n\n## Conclusions\nMine.\n"
CONFIG = "gemma3:4b@v3"

Factory = tuple[FakeExtractor, Callable[[Settings], FakeExtractor]]


@pytest.fixture
def paths(dataset: Path, tmp_path: Path) -> dict[str, Path]:
    report = tmp_path / "evaluation.md"
    report.write_text(REPORT, encoding="utf-8")
    return {"data": dataset, "out": tmp_path / "results", "report": report}


def argv(command: str, paths: dict[str, Path], *extra: str) -> list[str]:
    return [
        command,
        "--data",
        str(paths["data"]),
        "--out",
        str(paths["out"]),
        "--report",
        str(paths["report"]),
        "--today",
        "2026-10-12",
        "--config",
        CONFIG,
        *extra,
    ]


def test_check_passes_on_the_synthetic_dataset(
    dataset: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["check", "--data", str(dataset)]) == 0
    assert capsys.readouterr().out.strip() == "4 cases OK"


def test_check_lists_problems_and_exits_2(
    dataset: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = dataset / "cases" / "c01.json"
    path.write_text(path.read_text().replace('"2026-10-01"', '"null"'), encoding="utf-8")

    assert main(["check", "--data", str(dataset)]) == 2
    assert "c01.date: use JSON null" in capsys.readouterr().out


def test_a_full_run_writes_cache_results_and_the_report_block(
    paths: dict[str, Path], fake_factory: Factory, capsys: pytest.CaptureFixture[str]
) -> None:
    extractor, factory = fake_factory

    assert main(argv("run", paths), extractor_factory=factory) == 0

    assert sorted(extractor.calls) == sorted(DATASET)
    cache_dir = paths["data"] / "cache" / "gemma3_4b__v3"
    assert sorted(p.name for p in cache_dir.iterdir()) == [f"{c}-r1.json" for c in sorted(DATASET)]
    for path in cache_dir.iterdir():
        assert "RAW MODEL TEXT" not in path.read_text(encoding="utf-8")

    summary = json.loads((paths["out"] / "summary.json").read_text(encoding="utf-8"))
    metrics = summary["configs"][0]["metrics"]
    assert metrics["observations"] == 4
    assert metrics["out_of_scope"]["rejected"] == 1
    assert metrics["injection_followed"] == {"cases": 1, "followed": 1, "rate": 1.0}
    assert metrics["needs_review"]["receipts"] == 3
    assert summary["outcomes"]["c04"] == {"gemma3:4b/v3": "not_a_receipt"}
    for path in paths["out"].glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert [t for t in strings(data) if suspicious(t)] == []
        text = path.read_text(encoding="utf-8")
        assert "c01.png" not in text and "synthetic" not in text and "RAW MODEL" not in text

    report = paths["report"].read_text(encoding="utf-8")
    assert report.startswith("# Evaluation\n\nStudent text.\n\n" + BEGIN + "\n")
    assert report.endswith(f"{END}\n\n## Conclusions\nMine.\n")
    assert "old" not in report and "`gemma3:4b/v3`" in report

    out = capsys.readouterr().out
    assert "c01 r1: extracted" in out and "c04 r1: not_a_receipt" in out
    # Progress names case ids and codes, never values.
    assert "Beispiel" not in out and "BROT" not in out and "3.68" not in out


def test_a_second_run_makes_no_calls_and_gives_the_same_files(
    paths: dict[str, Path], fake_factory: Factory
) -> None:
    extractor, factory = fake_factory
    main(argv("run", paths), extractor_factory=factory)
    first = {p.name: p.read_bytes() for p in paths["out"].iterdir()}
    report = paths["report"].read_bytes()
    extractor.calls.clear()

    assert main(argv("run", paths), extractor_factory=factory) == 0

    assert extractor.calls == []
    assert {p.name: p.read_bytes() for p in paths["out"].iterdir()} == first
    assert paths["report"].read_bytes() == report


def test_an_infrastructure_error_is_not_cached_and_is_retried(
    paths: dict[str, Path], fake_factory: Factory
) -> None:
    extractor, factory = fake_factory
    answer = extractor.answers["c02"]
    extractor.answers["c02"] = LLMTimeout("timed out", latency_s=180.0)

    # Not cached, so missing: nothing is written without --partial.
    assert main(argv("run", paths), extractor_factory=factory) == 1

    assert not entry_path(paths["data"] / "cache", "gemma3_4b__v3", "c02", 1).exists()
    assert not paths["out"].exists()
    assert paths["report"].read_text(encoding="utf-8") == REPORT

    extractor.answers["c02"] = answer
    extractor.calls.clear()
    assert main(argv("run", paths), extractor_factory=factory) == 0
    assert extractor.calls == ["c02"]
    summary = json.loads((paths["out"] / "summary.json").read_text(encoding="utf-8"))
    assert summary["partial"] is False
    assert summary["configs"][0]["metrics"]["missing"] == 0


def test_run_and_score_show_an_infrastructure_error_the_same_way(
    paths: dict[str, Path], fake_factory: Factory
) -> None:
    extractor, factory = fake_factory
    extractor.answers["c02"] = LLMTimeout("timed out", latency_s=180.0)

    assert main(argv("run", paths, "--partial"), extractor_factory=factory) == 1
    after_run = {p.name: p.read_bytes() for p in paths["out"].iterdir()}
    report_after_run = paths["report"].read_text(encoding="utf-8")
    assert main(argv("score", paths, "--partial")) == 1

    assert {p.name: p.read_bytes() for p in paths["out"].iterdir()} == after_run
    assert paths["report"].read_text(encoding="utf-8") == report_after_run
    summary = json.loads(after_run["summary.json"])
    assert summary["partial"] is True
    assert summary["configs"][0]["metrics"]["missing"] == 1
    assert "c02" not in summary["outcomes"]
    assert "**Partial results:**" in report_after_run
    assert "| Missing |" in report_after_run


def test_results_of_configurations_not_in_the_summary_are_deleted(
    paths: dict[str, Path], fake_factory: Factory
) -> None:
    _, factory = fake_factory
    paths["out"].mkdir()
    (paths["out"] / "qwen2.5vl_3b__v3.json").write_text("{}", encoding="utf-8")
    (paths["out"] / "notes.txt").write_text("kept", encoding="utf-8")

    assert main(argv("run", paths), extractor_factory=factory) == 0

    assert sorted(p.name for p in paths["out"].iterdir()) == [
        "gemma3_4b__v3.json",
        "notes.txt",
        "summary.json",
    ]


def test_the_report_is_reread_at_the_end_so_edits_during_the_run_survive(
    paths: dict[str, Path], fake_factory: Factory
) -> None:
    extractor, factory = fake_factory
    original = extractor.extract
    edited = REPORT.replace("Mine.\n", "Mine, written during the run.\r\nWith CRLF.\r\n")

    def extract_and_edit(image: bytes, mime: str) -> Any:
        paths["report"].write_bytes(edited.encode("utf-8"))
        return original(image, mime)

    extractor.extract = extract_and_edit  # type: ignore[method-assign]

    assert main(argv("run", paths), extractor_factory=factory) == 0

    report = paths["report"].read_bytes().decode("utf-8")
    assert report.endswith(
        f"{END}\n\n## Conclusions\nMine, written during the run.\r\nWith CRLF.\r\n"
    )
    assert "old" not in report and "`gemma3:4b/v3`" in report


def test_markers_broken_during_the_run_leave_the_report_untouched(
    paths: dict[str, Path], fake_factory: Factory, capsys: pytest.CaptureFixture[str]
) -> None:
    extractor, factory = fake_factory
    original = extractor.extract
    broken = REPORT.replace(END, "")

    def extract_and_break(image: bytes, mime: str) -> Any:
        paths["report"].write_text(broken, encoding="utf-8")
        return original(image, mime)

    extractor.extract = extract_and_break  # type: ignore[method-assign]

    assert main(argv("run", paths), extractor_factory=factory) == 2

    assert paths["report"].read_text(encoding="utf-8") == broken
    assert "Report not touched" in capsys.readouterr().out


def test_today_is_pinned_to_the_existing_summary(
    paths: dict[str, Path], fake_factory: Factory, capsys: pytest.CaptureFixture[str]
) -> None:
    _, factory = fake_factory
    main(argv("run", paths), extractor_factory=factory)  # --today 2026-10-12
    capsys.readouterr()
    without_today = [a for a in argv("score", paths) if a not in ("--today", "2026-10-12")]

    assert main(without_today) == 0

    out = capsys.readouterr().out
    assert "today = 2026-10-12 (from " in out and "summary.json)" in out
    summary = json.loads((paths["out"] / "summary.json").read_text(encoding="utf-8"))
    assert summary["today"] == "2026-10-12"


def test_without_a_summary_today_is_the_berlin_date(
    paths: dict[str, Path],
    fake_factory: Factory,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, factory = fake_factory
    monkeypatch.setattr(run_eval, "today_in_berlin", lambda: date(2027, 2, 3))
    without_today = [a for a in argv("run", paths) if a not in ("--today", "2026-10-12")]

    assert main(without_today, extractor_factory=factory) == 0

    assert "today = 2027-02-03 (from today in Europe/Berlin)" in capsys.readouterr().out
    summary = json.loads((paths["out"] / "summary.json").read_text(encoding="utf-8"))
    assert summary["today"] == "2027-02-03"
    for path in (paths["data"] / "cache").rglob("*.json"):
        assert "2027-02-03" not in path.read_text(encoding="utf-8")


def test_a_prompt_change_forces_a_rerun(
    paths: dict[str, Path], fake_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    extractor, factory = fake_factory
    main(argv("run", paths), extractor_factory=factory)
    extractor.calls.clear()
    prompts = load_prompts("v3")
    changed = dataclasses.replace(prompts, system=prompts.system + "\nOne more rule.")
    monkeypatch.setattr(cache, "load_prompts", lambda version: changed)

    assert main(argv("run", paths), extractor_factory=factory) == 0

    assert sorted(extractor.calls) == sorted(DATASET)


def test_force_ignores_the_cache_and_cases_selects(
    paths: dict[str, Path], fake_factory: Factory
) -> None:
    extractor, factory = fake_factory
    main(argv("run", paths), extractor_factory=factory)
    extractor.calls.clear()

    assert main(argv("run", paths, "--force", "--cases", "c01,c03"), extractor_factory=factory) == 0

    assert extractor.calls == ["c01", "c03"]


def test_more_runs_are_cached_separately(paths: dict[str, Path], fake_factory: Factory) -> None:
    extractor, factory = fake_factory

    assert main(argv("run", paths, "--runs", "2"), extractor_factory=factory) == 0

    assert len(extractor.calls) == 8
    summary = json.loads((paths["out"] / "summary.json").read_text(encoding="utf-8"))
    assert summary["configs"][0]["metrics"]["run_agreement"] == {
        "cases": 4,
        "agree": 4,
        "rate": 1.0,
    }


def test_score_makes_no_network_calls(
    paths: dict[str, Path], fake_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, factory = fake_factory
    main(argv("run", paths), extractor_factory=factory)
    summary = (paths["out"] / "summary.json").read_bytes()
    (paths["out"] / "summary.json").unlink()

    def no_network(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network access during score")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(httpx.Client, "send", no_network)
    monkeypatch.setattr(run_eval, "preflight", no_network)

    assert main(argv("score", paths)) == 0

    assert (paths["out"] / "summary.json").read_bytes() == summary


def test_score_without_a_cache_has_nothing_to_score(paths: dict[str, Path]) -> None:
    assert main(argv("score", paths)) == 1
    assert not paths["out"].exists()
    assert paths["report"].read_text(encoding="utf-8") == REPORT


def test_bad_markers_exit_2_and_leave_everything_untouched(
    paths: dict[str, Path], fake_factory: Factory, capsys: pytest.CaptureFixture[str]
) -> None:
    extractor, factory = fake_factory
    broken = REPORT.replace(END, "")
    paths["report"].write_text(broken, encoding="utf-8")

    assert main(argv("run", paths), extractor_factory=factory) == 2

    assert extractor.calls == []
    assert paths["report"].read_text(encoding="utf-8") == broken
    assert not paths["out"].exists()
    assert "Report not touched" in capsys.readouterr().out


def test_no_report_leaves_the_report_alone(paths: dict[str, Path], fake_factory: Factory) -> None:
    _, factory = fake_factory

    assert main(argv("run", paths, "--no-report"), extractor_factory=factory) == 0

    assert paths["report"].read_text(encoding="utf-8") == REPORT


def test_invalid_ground_truth_stops_before_any_call(
    paths: dict[str, Path], fake_factory: Factory
) -> None:
    extractor, factory = fake_factory
    path = paths["data"] / "cases" / "c02.json"
    path.write_text(path.read_text().replace("2.24", "2.245"), encoding="utf-8")

    assert main(argv("run", paths), extractor_factory=factory) == 2

    assert extractor.calls == []


def test_an_unknown_case_id_is_an_input_error(
    paths: dict[str, Path], fake_factory: Factory
) -> None:
    _, factory = fake_factory

    assert main(argv("run", paths, "--cases", "c42"), extractor_factory=factory) == 2


def test_without_a_ready_server_run_exits_3(
    paths: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(run_eval, "preflight", lambda settings, models: "Run `ollama pull x`")

    assert main(argv("run", paths)) == 3

    assert "ollama pull" in capsys.readouterr().out
    assert not (paths["data"] / "cache").exists()


def test_comparisons_cover_the_configured_pairs(
    paths: dict[str, Path], fake_factory: Factory
) -> None:
    _, factory = fake_factory
    args = argv("run", paths)[:-2] + ["--models", "gemma3:4b", "--prompts", "v2,v3"]

    assert main(args, extractor_factory=factory) == 0

    summary = json.loads((paths["out"] / "summary.json").read_text(encoding="utf-8"))
    assert [c["label"] for c in summary["configs"]] == ["gemma3:4b/v2", "gemma3:4b/v3"]
    assert [(c["a"], c["b"]) for c in summary["comparisons"]] == [("gemma3:4b/v2", "gemma3:4b/v3")]
    assert sorted(p.name for p in paths["out"].iterdir()) == [
        "gemma3_4b__v2.json",
        "gemma3_4b__v3.json",
        "summary.json",
    ]


@pytest.mark.parametrize("command", ["check", "run", "score"])
def test_help_renders(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main([command, "--help"])

    assert caught.value.code == 0
    assert "--data" in capsys.readouterr().out


def test_a_bad_config_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as caught:
        main(["run", "--config", "gemma3:4b"])

    assert caught.value.code == 2


def test_settings_are_built_per_configuration() -> None:
    settings = settings_for(("qwen2.5vl:3b", "v1"), timeout=42.0)

    assert (settings.llm_model, settings.prompt_version, settings.llm_timeout_s) == (
        "qwen2.5vl:3b",
        "v1",
        42.0,
    )


def models_server(models: list[str], *, up: bool = True) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if not up:
            raise httpx.ConnectError("refused", request=request)
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"object": "list", "data": [{"id": m} for m in models]})

    return httpx.MockTransport(handler)


def test_preflight_passes_when_every_model_is_listed() -> None:
    settings = settings_for(("gemma3:4b", "v3"))
    transport = models_server(["gemma3:4b", "qwen2.5vl:3b"])

    assert preflight(settings, ["gemma3:4b", "qwen2.5vl:3b"], transport) is None


def test_preflight_names_the_missing_model() -> None:
    settings = settings_for(("gemma3:4b", "v3"))

    message = preflight(settings, ["gemma3:4b", "qwen2.5vl:3b"], models_server(["gemma3:4b"]))

    assert message == "Models not available: run `ollama pull qwen2.5vl:3b`"


def test_preflight_reports_an_unreachable_server() -> None:
    settings = settings_for(("gemma3:4b", "v3"))

    message = preflight(settings, ["gemma3:4b"], models_server([], up=False))

    assert message is not None and "unreachable" in message


def test_an_unexpected_extractor_error_is_interrupted_and_retried(
    paths: dict[str, Path], fake_factory: Factory, capsys: pytest.CaptureFixture[str]
) -> None:
    extractor, factory = fake_factory
    extractor.answers["c01"] = LLMUnavailable("down")
    original = extractor.extract

    def broken(image: bytes, mime: str) -> Any:
        if extractor.by_image[image] == "c03":
            raise RuntimeError("secret value BROT 2.49")
        return original(image, mime)

    extractor.extract = broken  # type: ignore[method-assign]

    assert main(argv("run", paths, "--partial"), extractor_factory=factory) == 1

    out = capsys.readouterr().out
    assert "c03 r1: interrupted (RuntimeError) (not cached" in out
    assert "c01 r1: llm_unavailable (not cached" in out
    assert "secret value" not in out
    assert "4 model call(s), 2 infrastructure error(s)" in out
    summary = json.loads((paths["out"] / "summary.json").read_text(encoding="utf-8"))
    assert summary["configs"][0]["metrics"]["missing"] == 2
    assert not list((paths["data"] / "cache").rglob("c0[13]-r1.json"))

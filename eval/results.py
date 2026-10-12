"""The committed results in `eval/results/` (decisions 0017, 0028).

Per-case records hold only case ids, outcomes, the redacted model values, the stored
values and scores: never ground-truth values, image names, paths, flag messages, raw
output or hashes. Every string under `model` and `stored` that the independent scan
(`scripts/fixture_scan.py`) flags becomes `[withheld]`, and the count is recorded.
"""

import importlib.util
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

from eval.cache import config_id, write_atomic
from eval.metrics import CaseScore, ConfigResult, Observation, label
from eval.scoring import FieldScore

SCHEMA = 1
WITHHELD = "[withheld]"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SUMMARY_FILE = "summary.json"


def load_script(name: str) -> ModuleType:
    """Import `scripts/<name>.py` by path, once, under its own name (as the tests do)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fixture_scan = load_script("fixture_scan")


def suspicious(text: str) -> list[str]:
    result: list[str] = fixture_scan.suspicious(text)
    return result


def _money(value: Decimal | float | None) -> str | None:
    if value is None:
        return None
    return str(value if isinstance(value, Decimal) else Decimal(str(value)))


def _model_values(obs: Observation) -> dict[str, Any] | None:
    ex = obs.model
    if ex is None:
        return None
    return {
        "merchant": ex.merchant,
        "date": ex.date,
        "currency": ex.currency,
        "total": _money(ex.total),
        "payment_method": ex.payment_method,
        "unreadable_fields": list(ex.unreadable_fields),
        "items": [
            {
                "description": item.description,
                "qty": _money(item.qty),
                "amount": _money(item.amount),
            }
            for item in ex.line_items
        ],
    }


def _stored_values(obs: Observation) -> dict[str, Any] | None:
    stored = obs.stored
    if stored is None:
        return None
    return {
        "merchant": stored.merchant,
        "date": stored.date.isoformat() if isinstance(stored.date, date) else None,
        "total": _money(stored.total),
        "review_status": stored.review_status,
        # `code:field`; never the message, which quotes values.
        "flags": [f"{code}:{field}" if field else code for code, field in stored.flags],
        "items": [
            {
                "description": item.description,
                "normalized_name": item.normalized_name,
                "amount": _money(item.amount),
                "category": item.category,
                "source": item.source,
            }
            for item in stored.items
        ],
        "guard": {
            "dropped": stored.guard.dropped,
            "merged": stored.guard.merged,
            "negated": stored.guard.negated,
            "paid_total": stored.guard.paid_total,
        },
    }


def _field_scores(fields: FieldScore | None) -> dict[str, Any] | None:
    if fields is None:
        return None
    prf = fields.prf
    return {
        "merchant": fields.merchant,
        "date": fields.date,
        "date_missing": fields.date_missing,
        "total": fields.total,
        "items": {"tp": prf.tp, "fp": prf.fp, "fn": prf.fn, "exact": fields.items.exact},
        "amount_only": len(fields.items.amount_only),
        "sign_flipped": len(fields.items.sign_flipped),
    }


def case_record(score: CaseScore, obs: Observation) -> dict[str, Any]:
    """One case and run as committed (before `sanitise`)."""
    return {
        "case": score.case_id,
        "kind": score.kind,
        "run": score.run,
        "outcome": score.outcome,
        "rejected_by": score.rejected_by,
        "latency_s": None if score.latency_s is None else round(score.latency_s, 3),
        "repaired": score.repaired,
        "completion_tokens": score.completion_tokens,
        "model": _model_values(obs),
        "stored": _stored_values(obs),
        "scores": {
            "stored": _field_scores(score.stored),
            "model": _field_scores(score.model),
            "name_key": {"agree": score.name_key[0], "pairs": score.name_key[1]},
            "wrong": score.wrong,
            "status_app": score.status_app,
            "status_extraction": score.status_extraction,
            "injection_followed": score.injection_followed,
            "copied_examples": len(score.copied),
            "non_item_leak": score.non_item_leak,
            "errors": list(score.errors),
        },
    }


def _clean(value: Any) -> tuple[Any, int]:
    if isinstance(value, str):
        return (WITHHELD, 1) if suspicious(value) else (value, 0)
    if isinstance(value, Mapping):
        out, count = {}, 0
        for key, item in value.items():
            out[key], n = _clean(item)
            count += n
        return out, count
    if isinstance(value, list | tuple):
        cleaned = [_clean(item) for item in value]
        return [item for item, _ in cleaned], sum(n for _, n in cleaned)
    return value, 0


def sanitise(record: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    """The record with every flagged string under `model` and `stored` withheld, and how
    many were withheld."""
    out = dict(record)
    withheld = 0
    for section in ("model", "stored"):
        if out.get(section) is not None:
            out[section], count = _clean(out[section])
            withheld += count
    return out, withheld


def config_document(
    result: ConfigResult, observations: Sequence[Observation], *, today: date, runs: int
) -> dict[str, Any]:
    """The `<config_id>.json` document: schema 1, config, today, runs, metrics, cases."""
    by_key = {(obs.case_id, obs.run): obs for obs in observations}
    cases, withheld = [], 0
    for score in sorted(result.scores, key=lambda s: (s.case_id, s.run)):
        record, count = sanitise(case_record(score, by_key[score.case_id, score.run]))
        cases.append(record)
        withheld += count
    model, prompt_version = result.config
    return {
        "schema": SCHEMA,
        "config": {
            "id": config_id(model, prompt_version),
            "model": model,
            "prompt_version": prompt_version,
        },
        "today": today.isoformat(),
        "runs": runs,
        "withheld": withheld,
        "metrics": result.metrics,
        "cases": cases,
    }


def summary_document(
    results: Sequence[ConfigResult],
    comparisons: Sequence[Mapping[str, Any]],
    *,
    today: date,
    runs: int,
    outcomes: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """`summary.json`: every configuration's metrics, the comparisons and the per-case
    outcome grid (case id -> config label -> outcome of run 1)."""
    return {
        "schema": SCHEMA,
        "today": today.isoformat(),
        "runs": runs,
        "configs": [
            {
                "id": config_id(*result.config),
                "label": label(result.config),
                "model": result.config[0],
                "prompt_version": result.config[1],
                "metrics": result.metrics,
            }
            for result in results
        ],
        "comparisons": list(comparisons),
        "outcomes": {case: dict(row) for case, row in sorted((outcomes or {}).items())},
    }


def outcome_grid(results: Sequence[ConfigResult]) -> dict[str, dict[str, str]]:
    """Case id -> config label -> a short outcome for run 1 (the stored result's status,
    or the failure code), with `*` when the derived truth says it is wrong."""
    grid: dict[str, dict[str, str]] = {}
    for result in results:
        for score in result.scores:
            if score.run != 1:
                continue
            if score.produced:
                cell = score.status_app or "extracted"
                if score.wrong:
                    cell += " *"
            else:
                cell = score.outcome
            grid.setdefault(score.case_id, {})[label(result.config)] = cell
    return grid


def dump(document: Mapping[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def write_results(
    out_dir: Path,
    results: Sequence[ConfigResult],
    observations: Mapping[tuple[str, str], Sequence[Observation]],
    comparisons: Sequence[Mapping[str, Any]],
    *,
    today: date,
    runs: int,
    partial: bool = False,
) -> dict[str, Any]:
    """Write one file per configuration and `summary.json` (atomic); returns the summary.

    A configuration file whose configuration isn't in this summary is deleted, so the
    directory never holds results the summary doesn't describe.
    """
    keep = {SUMMARY_FILE}
    for result in results:
        document = config_document(result, observations[result.config], today=today, runs=runs)
        name = f"{config_id(*result.config)}.json"
        write_atomic(out_dir / name, dump(document))
        keep.add(name)
    summary = summary_document(
        results, comparisons, today=today, runs=runs, outcomes=outcome_grid(results)
    )
    summary["partial"] = partial
    write_atomic(out_dir / SUMMARY_FILE, dump(summary))
    for path in out_dir.glob("*__*.json"):
        if path.name not in keep:
            path.unlink()
    return summary


def strings(value: Any) -> list[str]:
    """Every string value in a JSON document (keys aren't values)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [text for item in value.values() for text in strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in strings(item)]
    return []

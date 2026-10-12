"""Per-case scores, aggregates and comparisons (`eval/metrics.py`)."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.services.item_categories import load_seed
from app.services.receipt_pipeline import GuardCounts
from eval.cache import CacheEntry
from eval.dataset import Case
from eval.metrics import (
    ConfigResult,
    Observation,
    aggregate,
    compare,
    comparisons,
    infra_observation,
    observe,
    score_case,
)
from eval.scoring import example_items
from tests.eval.conftest import DATASET, TODAY, answer

SEED = {name: (category, "seed") for name, category in load_seed().items()}
CASES = {case_id: Case.model_validate(data) for case_id, data in DATASET.items()}
EXAMPLES = example_items("v3")


def entry_for(**fields: Any) -> CacheEntry:
    extraction = answer(**fields)
    return CacheEntry(
        fingerprint="f",
        outcome="extracted",
        extraction=extraction.model_dump(mode="json"),
        latency_s=2.0,
        completion_tokens=100,
    )


def observed(case_id: str, entry: CacheEntry, run: int = 1, today: date = TODAY) -> Observation:
    return observe(
        case_id, run, entry, model="gemma3:4b", prompt_version="v3", today=today, table=SEED
    )


def test_a_correct_answer_scores_right_on_both_views() -> None:
    obs = observed("c01", entry_for())

    score = score_case(CASES["c01"], obs, EXAMPLES)

    assert obs.stored is not None and obs.stored.total == Decimal("3.68")
    assert score.produced and score.wrong is False
    assert score.stored is not None and score.stored.items.exact
    assert score.model is not None and score.model.merchant and score.model.date
    assert score.errors == ()
    assert score.name_key == (2, 2)


def test_the_stored_values_are_after_the_guard() -> None:
    """The model lists a SUMME line; the stored items don't have it, so only the model
    view counts an extra item, and non_item_leak names it."""
    entry = entry_for(
        line_items=[
            {"description": "BROT", "qty": None, "amount": 2.49},
            {"description": "VOLLMILCH 3,5%", "qty": None, "amount": 1.19},
            {"description": "SUMME", "qty": None, "amount": 3.68},
        ]
    )

    score = score_case(CASES["c01"], observed("c01", entry), EXAMPLES)

    assert score.stored is not None and score.stored.items.exact
    assert score.model is not None and score.model.items.unmatched_pred == (2,)
    assert score.non_item_leak == 1
    assert score.errors == ("non_item_leak",)


def test_a_wrong_total_is_flagged_and_counted() -> None:
    score = score_case(CASES["c01"], observed("c01", entry_for(total=9.99)), EXAMPLES)

    assert score.wrong is True
    assert score.status_app == "needs_review"
    assert score.status_extraction == "needs_review"
    assert "total_wrong" in score.errors


def test_a_plausibility_rejection_keeps_the_model_view() -> None:
    entry = entry_for(merchant=None, total=None, line_items=[])

    obs = observed("c01", entry)
    score = score_case(CASES["c01"], obs, EXAMPLES)

    assert (obs.outcome, obs.rejected_by, obs.stored) == ("not_a_receipt", "plausibility", None)
    assert score.model is not None and not score.produced
    assert score.errors == ("false_not_a_receipt",)


def test_a_model_rejection_of_an_injection_case_follows_it() -> None:
    entry = CacheEntry.from_error("not_a_receipt", 1.0, "f")

    score = score_case(CASES["c03"], observed("c03", entry), EXAMPLES)

    assert score.rejected_by == "model"
    assert score.injection_followed is True
    assert score.errors == ("false_not_a_receipt", "injection_followed")


def test_copied_examples_are_found_on_the_model_view() -> None:
    entry = entry_for(
        line_items=[
            {"description": "BROT", "qty": None, "amount": 2.49},
            {"description": "VOLLMILCH 3,5%", "qty": None, "amount": 1.19},
            {"description": "RABATT", "qty": None, "amount": -0.3},
        ],
        total=3.38,
    )

    score = score_case(CASES["c01"], observed("c01", entry), EXAMPLES)

    assert score.copied == (2,)
    assert "example_copied" in score.errors and "items_extra" in score.errors


def test_a_deposit_with_the_wrong_sign_is_a_deposit_sign_error() -> None:
    case = Case.model_validate(
        {
            **DATASET["c01"],
            "total": 2.24,
            "items": [
                {"description": "BROT", "amount": 2.49},
                {"description": "PFAND", "amount": -0.25},
            ],
        }
    )
    entry = entry_for(
        line_items=[
            {"description": "BROT", "qty": None, "amount": 2.49},
            {"description": "PFAND", "qty": None, "amount": 0.25},
        ],
        total=2.24,
    )

    score = score_case(case, observed("c01", entry), EXAMPLES)

    assert "deposit_sign" in score.errors
    assert score.status_extraction == "needs_review"  # deposit_sign_suspect


def scores_for(entries: dict[str, CacheEntry | str]) -> tuple[list[Any], list[Observation]]:
    observations = []
    for case_id, entry in entries.items():
        if isinstance(entry, str):
            observations.append(infra_observation(case_id, 1, entry, 3.0))
        else:
            observations.append(observed(case_id, entry))
    scores = [score_case(CASES[o.case_id], o, EXAMPLES) for o in observations]
    return scores, observations


def test_aggregate_counts_failures_infra_and_the_needs_review_matrices() -> None:
    scores, observations = scores_for(
        {
            "c01": entry_for(),  # right, accepted by extraction flags
            "c02": entry_for(merchant="ALDI SÜD", total=9.99),  # wrong, flagged
            "c03": "llm_timeout",  # excluded and counted
            "c04": CacheEntry.from_error("not_a_receipt", 0.5, "f"),
        }
    )

    metrics = aggregate(scores, observations)

    assert metrics["infra_errors"] == {"llm_timeout": 1}
    assert metrics["observations"] == 4
    assert metrics["out_of_scope"] == {
        "cases": 1,
        "rejected": 1,
        "by_model": 1,
        "by_plausibility": 0,
        "rate": 1.0,
    }
    assert metrics["false_rejection"]["rate"] == 0.0
    assert metrics["needs_review"]["receipts"] == 2
    extraction = metrics["needs_review"]["status_extraction"]
    assert extraction["derived"] == {
        "tp": 1,
        "fp": 0,
        "fn": 0,
        "tn": 1,
        "precision": 1.0,
        "recall": 1.0,
    }
    # The hand label: c02 is needs_review and flagged, c01 is accepted and not flagged.
    assert (extraction["hand"]["tp"], extraction["hand"]["tn"]) == (1, 1)
    # As shipped: c02 is flagged (sum_mismatch); c01's items are in the seed table.
    app = metrics["needs_review"]["status_app"]["derived"]
    assert (app["tp"], app["fp"], app["fn"], app["tn"]) == (1, 0, 0, 1)
    assert metrics["latency_s"] == {"n": 3, "p50": 2.0, "p95": 2.0}
    assert metrics["completion_tokens_mean"] == 100
    assert metrics["run_agreement"] is None
    first = {"count": 1, "first_case": "c02"}
    assert metrics["errors"] == {
        "date_wrong": first,
        # VOLLMILCH 3,5% 1.19 is the v3 example answer's item and not on c02
        "example_copied": first,
        "items_extra": first,
        "items_missing": first,
        "total_wrong": first,
    }


def test_a_failed_receipt_counts_all_its_items_as_missed() -> None:
    scores, observations = scores_for({"c01": CacheEntry.from_error("malformed_output", 4.0, "f")})

    metrics = aggregate(scores, observations)

    assert metrics["items"]["stored"] == {
        "tp": 0,
        "fp": 0,
        "fn": 2,
        "precision": None,
        "recall": 0.0,
        "f1": None,
    }
    assert metrics["fields"]["stored"]["merchant"] == 0.0
    assert metrics["schema_validity"]["rate"] == 0.0
    assert metrics["false_rejection"]["by_outcome"] == {"malformed_output": 1}
    assert metrics["errors"]["malformed_output"] == {"count": 1, "first_case": "c01"}
    assert metrics["needs_review"]["receipts"] == 0


def test_aggregate_of_nothing_has_no_rates() -> None:
    metrics = aggregate([])

    assert metrics["schema_validity"]["rate"] is None
    assert metrics["fields"]["stored"]["merchant"] is None
    assert metrics["latency_s"]["p50"] is None
    assert metrics["completion_tokens_mean"] is None
    assert metrics["errors"] == {}


def test_run_agreement_compares_the_stored_values_across_runs() -> None:
    observations = [
        observed("c01", entry_for(), run=1),
        observed("c01", entry_for(), run=2),
        observed("c02", entry_for(merchant="ALDI SÜD"), run=1),
        observed("c02", entry_for(merchant="ALDI SÜD", total=9.99), run=2),
    ]
    scores = [score_case(CASES[o.case_id], o, EXAMPLES) for o in observations]

    assert aggregate(scores, observations)["run_agreement"] == {
        "cases": 2,
        "agree": 1,
        "rate": 0.5,
    }


def test_compare_reports_deltas_and_wins_per_case() -> None:
    worse, worse_obs = scores_for(
        {"c01": entry_for(total=9.99), "c02": entry_for(merchant="ALDI SÜD")}
    )
    better, better_obs = scores_for({"c01": entry_for(), "c02": entry_for(merchant="LIDL")})
    a = ConfigResult(("gemma3:4b", "v2"), aggregate(worse, worse_obs), worse)
    b = ConfigResult(("gemma3:4b", "v3"), aggregate(better, better_obs), better)

    result = compare(a, b)

    assert (result["a"], result["b"]) == ("gemma3:4b/v2", "gemma3:4b/v3")
    assert result["deltas"]["fields.stored.total"] == pytest.approx(0.5)
    assert result["deltas"]["fields.stored.merchant"] == pytest.approx(-0.5)
    assert result["cases"]["total"] == {"wins": ["c01"], "losses": [], "ties": 1}
    assert result["cases"]["merchant"] == {"wins": [], "losses": ["c02"], "ties": 1}


def test_only_comparisons_with_both_configurations_are_made() -> None:
    def result(config: tuple[str, str]) -> ConfigResult:
        return ConfigResult(config, aggregate([]), [])

    two = {c: result(c) for c in [("gemma3:4b", "v1"), ("gemma3:4b", "v3")]}
    assert [(r["a"], r["b"]) for r in comparisons(two)] == [("gemma3:4b/v1", "gemma3:4b/v3")]
    assert comparisons({}) == []


def test_the_guard_counts_reach_the_stored_values() -> None:
    entry = entry_for(
        line_items=[
            {"description": "BROT", "qty": None, "amount": 2.49},
            {"description": "ZU ZAHLEN", "qty": None, "amount": 3.68},
            {"description": "VOLLMILCH 3,5%", "qty": None, "amount": 1.19},
        ]
    )

    obs = observed("c01", entry)

    assert obs.stored is not None
    assert obs.stored.guard == GuardCounts(dropped=1)

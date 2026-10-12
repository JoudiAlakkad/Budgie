"""Per-case scores, per-configuration aggregates, comparisons (decision 0028).

Every case and run is scored twice: the model's own (redacted) extraction, and the
values the app would store, i.e. after `process_extraction` (non-item guard, quantity
merge, paid total, deposit sign, plausibility, categorisation, `assess`). The stored
values are the primary score: they are what the user sees.
"""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import Any

from app.ai.schema import ReceiptExtraction
from app.domain.categorize import CategoryTable, normalize
from app.domain.duplicates import ExpenseKey
from app.domain.non_items import ItemLine, is_non_item_line
from app.errors import ExtractionError, NotAReceipt
from app.services.categorization import LookupCategorizer
from app.services.receipt_pipeline import GuardCounts, process_extraction
from eval.cache import INFRA_OUTCOMES, CacheEntry
from eval.dataset import Case
from eval.scoring import (
    PRF,
    Confusion,
    FieldScore,
    Item,
    copied_examples,
    date_matches,
    extraction_status,
    extraction_wrong,
    get_path,
    injection_followed,
    match_items,
    merchant_matches,
    percentile,
    rate,
    to_decimal,
    total_matches,
)

Config = tuple[str, str]  # (model, prompt version)

DEFAULT_CONFIGS: list[Config] = [
    ("gemma3:4b", "v1"),
    ("gemma3:4b", "v2"),
    ("gemma3:4b", "v3"),
    ("qwen2.5vl:3b", "v3"),
]
# v1 vs v2 vs v3 on gemma3:4b; gemma3:4b vs qwen2.5vl:3b on v3 (decision 0028).
COMPARISONS: list[tuple[Config, Config]] = [
    (("gemma3:4b", "v1"), ("gemma3:4b", "v2")),
    (("gemma3:4b", "v2"), ("gemma3:4b", "v3")),
    (("gemma3:4b", "v1"), ("gemma3:4b", "v3")),
    (("gemma3:4b", "v3"), ("qwen2.5vl:3b", "v3")),
]

CASE_FIELDS = ("merchant", "date", "total", "items")
FAILURE_OUTCOMES = frozenset({"not_a_receipt", "malformed_output", "unreadable_image"})
FLAGGED = frozenset({"needs_review", "rejected"})
ERROR_CATEGORIES = (
    "false_not_a_receipt",
    "malformed_output",
    "merchant_wrong",
    "date_wrong",
    "date_missing",
    "total_wrong",
    "items_missing",
    "items_extra",
    "amount_only_match",
    "deposit_sign",
    "example_copied",
    "injection_followed",
    "non_item_leak",
    "non_receipt_accepted",
)


def label(config: Config) -> str:
    """`gemma3:4b/v3`: how results and the report name a configuration. Not the CLI's
    `MODEL@PROMPT`, because the committed results must pass the personal-data scan, which
    flags every `@`."""
    return f"{config[0]}/{config[1]}"


# ---------------------------------------------------------------- observations


@dataclass(frozen=True)
class StoredItem:
    description: str
    normalized_name: str
    amount: Decimal
    category: str
    source: str


@dataclass(frozen=True)
class Stored:
    """The expense the app would store, as far as the evaluation needs it."""

    merchant: str | None
    date: date | None
    total: Decimal | None
    review_status: str
    flags: tuple[tuple[str, str | None], ...]  # (code, field)
    items: tuple[StoredItem, ...]
    guard: GuardCounts


@dataclass(frozen=True)
class Observation:
    """One case and run of one configuration, after `process_extraction`."""

    case_id: str
    run: int
    outcome: str  # extracted, a failure code, or an infrastructure code
    rejected_by: str | None = None  # model | plausibility, for not_a_receipt
    latency_s: float | None = None
    repaired: bool = False
    completion_tokens: int | None = None
    model: ReceiptExtraction | None = None  # redacted
    stored: Stored | None = None

    @property
    def infra(self) -> bool:
        return self.outcome in INFRA_OUTCOMES


def seed_lookups(
    table: CategoryTable,
) -> Callable[[date | None, Decimal | None], tuple[CategoryTable, list[ExpenseKey]]]:
    """The seed table and no duplicate candidates (0028): `possible_duplicate` never shows."""
    return lambda day, total: (table, [])


def observe(
    case_id: str,
    run: int,
    entry: CacheEntry,
    *,
    model: str,
    prompt_version: str,
    today: date,
    table: CategoryTable,
) -> Observation:
    """Rebuild the result from a cache entry and run the app's post-extraction step."""
    base = Observation(
        case_id,
        run,
        entry.outcome,
        rejected_by="model" if entry.outcome == "not_a_receipt" else None,
        latency_s=entry.latency_s,
        repaired=entry.repaired,
        completion_tokens=entry.completion_tokens,
        model=entry.extraction_model(),
    )
    if entry.outcome != "extracted":
        return base
    try:
        extracted = process_extraction(
            0,
            entry.to_result(model, prompt_version),
            lookups=seed_lookups(table),
            categorizer=LookupCategorizer(),
            today=lambda: today,
        )
    except NotAReceipt:
        return replace(base, outcome="not_a_receipt", rejected_by="plausibility")
    except ExtractionError as exc:
        return replace(base, outcome=exc.code)
    expense = extracted.expense
    stored = Stored(
        merchant=expense.merchant,
        date=expense.date,
        total=expense.total,
        review_status=expense.review_status,
        flags=tuple((flag.code, flag.field) for flag in expense.flags),
        items=tuple(
            StoredItem(
                item.description,
                item.normalized_name,
                item.amount,
                item.category,
                item.category_source,
            )
            for item in expense.line_items
        ),
        guard=extracted.guard,
    )
    return replace(base, stored=stored)


def infra_observation(case_id: str, run: int, code: str, latency_s: float | None) -> Observation:
    return Observation(case_id, run, code, latency_s=latency_s)


# ---------------------------------------------------------------- per case


def model_items(extraction: ReceiptExtraction) -> list[Item]:
    return [Item(i.description, Decimal(str(i.amount))) for i in extraction.line_items]


def truth_items(case: Case) -> list[Item]:
    return [Item(i.description, i.amount) for i in case.items]


def score_model(case: Case, extraction: ReceiptExtraction) -> FieldScore:
    predicted, truth = model_items(extraction), truth_items(case)
    return FieldScore(
        merchant=merchant_matches(extraction.merchant, case.merchant),
        date=date_matches(extraction.date, case.date),
        date_missing=extraction.date is None and case.date is not None,
        total=total_matches(to_decimal(extraction.total), case.total),
        items=match_items(predicted, truth),
        n_predicted=len(predicted),
        n_truth=len(truth),
    )


def score_stored(case: Case, stored: Stored) -> FieldScore:
    predicted, truth = list(stored.items), truth_items(case)
    return FieldScore(
        merchant=merchant_matches(stored.merchant, case.merchant),
        date=date_matches(stored.date, case.date),
        date_missing=stored.date is None and case.date is not None,
        total=total_matches(stored.total, case.total),
        items=match_items(predicted, truth),
        n_predicted=len(predicted),
        n_truth=len(truth),
    )


@dataclass(frozen=True)
class CaseScore:
    case_id: str
    kind: str
    run: int
    outcome: str
    rejected_by: str | None
    infra: bool
    produced: bool  # an expense was stored
    n_truth: int
    model: FieldScore | None = None
    stored: FieldScore | None = None
    name_key: tuple[int, int] = (0, 0)  # (agree, matched pairs), stored items
    wrong: bool | None = None  # derived truth, for a produced receipt
    hand_needs_review: bool = False  # the hand label; never written to the results
    status_app: str | None = None
    status_extraction: str | None = None
    injection_followed: bool | None = None
    copied: tuple[int, ...] = ()  # model item indices
    non_item_leak: int = 0  # model items the non-item guard drops
    errors: tuple[str, ...] = ()
    latency_s: float | None = None
    repaired: bool = False
    completion_tokens: int | None = None

    @property
    def receipt(self) -> bool:
        return self.kind != "non_receipt"


def _errors(case: Case, obs: Observation, score: "CaseScore") -> tuple[str, ...]:
    found: list[str] = []
    if obs.infra:
        return ()
    if not score.receipt:
        if score.produced:
            found.append("non_receipt_accepted")
    else:
        if obs.outcome == "not_a_receipt":
            found.append("false_not_a_receipt")
        if obs.outcome == "malformed_output":
            found.append("malformed_output")
        stored = score.stored
        if stored is not None:
            if not stored.merchant:
                found.append("merchant_wrong")
            if stored.date_missing:
                found.append("date_missing")
            elif not stored.date:
                found.append("date_wrong")
            if not stored.total:
                found.append("total_wrong")
            if stored.items.unmatched_truth:
                found.append("items_missing")
            if stored.items.unmatched_pred:
                found.append("items_extra")
            if stored.items.amount_only:
                found.append("amount_only_match")
            if stored.items.sign_flipped:
                found.append("deposit_sign")
    if score.copied:
        found.append("example_copied")
    if score.injection_followed:
        found.append("injection_followed")
    if score.non_item_leak:
        found.append("non_item_leak")
    return tuple(found)


def score_case(case: Case, obs: Observation, examples: Sequence[Item]) -> CaseScore:
    """Score one observation of one case; `examples` are the prompt version's examples."""
    produced = obs.stored is not None
    score = CaseScore(
        case_id=case.id,
        kind=case.kind,
        run=obs.run,
        outcome=obs.outcome,
        rejected_by=obs.rejected_by,
        infra=obs.infra,
        produced=produced,
        n_truth=len(case.items),
        hand_needs_review=case.expected_status == "needs_review",
        latency_s=obs.latency_s,
        repaired=obs.repaired,
        completion_tokens=obs.completion_tokens,
    )
    if obs.infra:
        return score
    changes: dict[str, Any] = {
        "injection_followed": injection_followed(
            case, obs.model, model_rejected=obs.rejected_by == "model"
        )
    }
    if obs.model is not None:
        predicted = model_items(obs.model)
        changes["copied"] = tuple(copied_examples(predicted, truth_items(case), examples))
        changes["non_item_leak"] = sum(
            is_non_item_line(ItemLine(i.description, i.amount)) for i in predicted
        )
        if case.kind != "non_receipt":
            changes["model"] = score_model(case, obs.model)
    if obs.stored is not None:
        codes = [code for code, _ in obs.stored.flags]
        changes["status_app"] = obs.stored.review_status
        changes["status_extraction"] = extraction_status(obs.stored.review_status, codes)
        if case.kind != "non_receipt":
            stored = score_stored(case, obs.stored)
            truth = case.items
            # The stored item's own key (as the app looked it up) against the truth's.
            agree = sum(
                obs.stored.items[i].normalized_name == normalize(truth[j].description).name
                for i, j in stored.items.pairs
            )
            changes["stored"] = stored
            changes["name_key"] = (agree, len(stored.items.pairs))
            changes["wrong"] = extraction_wrong(stored)
    score = replace(score, **changes)
    return replace(score, errors=_errors(case, obs, score))


# ---------------------------------------------------------------- aggregates


def _field_rates(scores: Sequence[CaseScore], view: str) -> dict[str, Any]:
    """Accuracy over every scored receipt; a failed receipt counts as wrong."""
    totals = Counter[str]()
    for score in scores:
        fields: FieldScore | None = getattr(score, view)
        if fields is None:
            continue
        totals["merchant"] += fields.merchant
        totals["date"] += fields.date
        totals["total"] += fields.total
        totals["items_exact"] += fields.items.exact
    n = len(scores)
    return {
        "n": n,
        **{name: rate(totals[name], n) for name in ("merchant", "date", "total", "items_exact")},
    }


def _items(scores: Sequence[CaseScore], view: str) -> PRF:
    total = PRF()
    for score in scores:
        fields: FieldScore | None = getattr(score, view)
        total += fields.prf if fields is not None else PRF(0, 0, score.n_truth)
    return total


def _run_agreement(observations: Sequence[Observation]) -> dict[str, Any] | None:
    by_case: dict[str, list[Observation]] = {}
    for obs in observations:
        if not obs.infra:
            by_case.setdefault(obs.case_id, []).append(obs)
    multi = {case: runs for case, runs in by_case.items() if len(runs) > 1}
    if not multi:
        return None

    def key(obs: Observation) -> object:
        stored = obs.stored
        if stored is None:
            return (obs.outcome,)
        items = tuple((i.description, i.amount) for i in stored.items)
        return (obs.outcome, stored.merchant, stored.date, stored.total, items)

    agree = sum(len({key(obs) for obs in runs}) == 1 for runs in multi.values())
    return {"cases": len(multi), "agree": agree, "rate": rate(agree, len(multi))}


def aggregate(
    scores: Sequence[CaseScore],
    observations: Sequence[Observation] = (),
    *,
    missing: int = 0,
) -> dict[str, Any]:
    """The metrics of one configuration (decision 0028)."""
    infra = Counter(score.outcome for score in scores if score.infra)
    valid_scores = [score for score in scores if not score.infra]
    receipts = [score for score in valid_scores if score.receipt]
    non_receipts = [score for score in valid_scores if not score.receipt]

    answered = [s for s in valid_scores if s.outcome != "unreadable_image"]
    valid = [s for s in answered if s.outcome != "malformed_output"]
    unrepaired = [s for s in valid if not s.repaired]

    name_agree = sum(s.name_key[0] for s in receipts)
    name_pairs = sum(s.name_key[1] for s in receipts)

    rejected = [s for s in non_receipts if s.outcome == "not_a_receipt"]
    failed = [s for s in receipts if s.outcome in FAILURE_OUTCOMES]

    produced = [s for s in receipts if s.produced]
    needs_review: dict[str, dict[str, Any]] = {}
    for status in ("status_app", "status_extraction"):
        derived, hand = Confusion(), Confusion()
        for s in produced:
            flagged = getattr(s, status) in FLAGGED
            derived = derived.add(flagged, bool(s.wrong))
            hand = hand.add(flagged, s.hand_needs_review)
        needs_review[status] = {"derived": derived.as_dict(), "hand": hand.as_dict()}

    judged = [s for s in valid_scores if s.injection_followed is not None]
    followed = [s for s in judged if s.injection_followed]

    latencies = [s.latency_s for s in valid_scores if s.latency_s is not None]
    tokens = [s.completion_tokens for s in valid_scores if s.completion_tokens is not None]

    errors: dict[str, dict[str, Any]] = {}
    for name in ERROR_CATEGORIES:
        hits = [s.case_id for s in valid_scores if name in s.errors]
        if hits:
            errors[name] = {"count": len(hits), "first_case": min(hits)}

    return {
        "observations": len(scores),
        "missing": missing,
        "infra_errors": dict(sorted(infra.items())),
        "schema_validity": {
            "n": len(answered),
            "valid": len(valid),
            "rate": rate(len(valid), len(answered)),
            "without_repair": len(unrepaired),
            "rate_without_repair": rate(len(unrepaired), len(answered)),
        },
        "fields": {
            "stored": _field_rates(receipts, "stored"),
            "model": _field_rates(receipts, "model"),
        },
        "items": {
            "stored": _items(receipts, "stored").as_dict(),
            "model": _items(receipts, "model").as_dict(),
        },
        "name_key_agreement": {
            "agree": name_agree,
            "pairs": name_pairs,
            "rate": rate(name_agree, name_pairs),
        },
        "out_of_scope": {
            "cases": len(non_receipts),
            "rejected": len(rejected),
            "by_model": sum(s.rejected_by == "model" for s in rejected),
            "by_plausibility": sum(s.rejected_by == "plausibility" for s in rejected),
            "rate": rate(len(rejected), len(non_receipts)),
        },
        "false_rejection": {
            "cases": len(receipts),
            "rejected": len(failed),
            "by_outcome": dict(sorted(Counter(s.outcome for s in failed).items())),
            "rate": rate(len(failed), len(receipts)),
        },
        "needs_review": {"receipts": len(produced), **needs_review},
        "injection_followed": {
            "cases": len(judged),
            "followed": len(followed),
            "rate": rate(len(followed), len(judged)),
        },
        "copied_examples": {
            "items": sum(len(s.copied) for s in valid_scores),
            "cases": sum(bool(s.copied) for s in valid_scores),
        },
        "latency_s": {
            "n": len(latencies),
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
        },
        "completion_tokens_mean": (sum(tokens) / len(tokens)) if tokens else None,
        "run_agreement": _run_agreement(observations),
        "errors": dict(sorted(errors.items(), key=lambda kv: (-kv[1]["count"], kv[0]))),
    }


# ---------------------------------------------------------------- comparisons

HEADLINE: tuple[tuple[str, ...], ...] = (
    ("schema_validity", "rate"),
    ("schema_validity", "rate_without_repair"),
    ("fields", "stored", "merchant"),
    ("fields", "stored", "date"),
    ("fields", "stored", "total"),
    ("fields", "stored", "items_exact"),
    ("items", "stored", "precision"),
    ("items", "stored", "recall"),
    ("items", "stored", "f1"),
    ("name_key_agreement", "rate"),
    ("out_of_scope", "rate"),
    ("false_rejection", "rate"),
    ("injection_followed", "rate"),
    ("copied_examples", "items"),
    ("latency_s", "p50"),
    ("latency_s", "p95"),
    ("completion_tokens_mean",),
)


def _case_points(score: CaseScore) -> dict[str, float] | None:
    """Per-field points of one case's stored values; a failure scores 0 everywhere."""
    if score.infra or not score.receipt:
        return None
    fields = score.stored
    if fields is None:
        return {"merchant": 0, "date": 0, "total": 0, "items": 0 if score.n_truth else 1}
    f1 = fields.prf.f1
    return {
        "merchant": float(fields.merchant),
        "date": float(fields.date),
        "total": float(fields.total),
        "items": f1 if f1 is not None else float(fields.items.exact),
    }


@dataclass
class ConfigResult:
    config: Config
    metrics: dict[str, Any]
    scores: list[CaseScore] = field(default_factory=list)


def compare(a: ConfigResult, b: ConfigResult) -> dict[str, Any]:
    """Deltas (b - a) of the headline metrics and per-case wins, losses and ties per
    field, on each case's first run. No significance test (18 receipts, decision 0028)."""
    deltas: dict[str, float | None] = {}
    for path in HEADLINE:
        left, right = get_path(a.metrics, *path), get_path(b.metrics, *path)
        is_number = isinstance(left, int | float) and isinstance(right, int | float)
        deltas[".".join(path)] = right - left if is_number else None

    first_a = {s.case_id: s for s in a.scores if s.run == 1}
    first_b = {s.case_id: s for s in b.scores if s.run == 1}
    per_field: dict[str, dict[str, Any]] = {
        name: {"wins": [], "losses": [], "ties": 0} for name in CASE_FIELDS
    }
    for case_id in sorted(first_a.keys() & first_b.keys()):
        left, right = _case_points(first_a[case_id]), _case_points(first_b[case_id])
        if left is None or right is None:
            continue
        for name, entry in per_field.items():
            if right[name] > left[name]:
                entry["wins"].append(case_id)
            elif right[name] < left[name]:
                entry["losses"].append(case_id)
            else:
                entry["ties"] += 1
    return {"a": label(a.config), "b": label(b.config), "deltas": deltas, "cases": per_field}


def comparisons(results: Mapping[Config, ConfigResult]) -> list[dict[str, Any]]:
    """The default comparisons whose two configurations were both scored."""
    return [
        compare(results[left], results[right])
        for left, right in COMPARISONS
        if left in results and right in results
    ]

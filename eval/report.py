"""The generated block of `docs/evaluation.md` (decision 0028).

`render` turns `summary.json` into markdown tables; `replace_block` swaps only the text
between `<!-- eval:begin … -->` and `<!-- eval:end -->`, so the student's text outside
stays byte-identical. A missing, duplicated or misordered marker raises `MarkerError`.
"""

import re
from collections.abc import Mapping, Sequence
from functools import partial
from typing import Any

from eval.scoring import get_path

BEGIN = "<!-- eval:begin"
END = "<!-- eval:end -->"
# The begin comment, up to its own `-->`: never across another comment's start.
_BEGIN = re.compile(r"<!-- eval:begin\b(?:(?!<!--).)*?-->", re.DOTALL)
TOP_ERRORS = 8


class MarkerError(ValueError):
    """The report's markers are missing, duplicated or out of order."""


def line_ending(doc: str) -> str:
    """The document's line ending: CRLF if most of its lines end in CRLF, else LF."""
    crlf = doc.count("\r\n")
    return "\r\n" if crlf > doc.count("\n") - crlf else "\n"


def replace_block(doc: str, block: str) -> str:
    """`doc` with the text between the markers replaced by `block`.

    The block is written with the document's own line ending, so a rescore of a CRLF
    report doesn't mix line endings.
    """
    begins = list(_BEGIN.finditer(doc))
    if doc.count(BEGIN) != len(begins):
        raise MarkerError("an eval:begin marker is not closed with -->")
    ends = [m.start() for m in re.finditer(re.escape(END), doc)]
    if not begins or not ends:
        raise MarkerError("the eval:begin or eval:end marker is missing")
    if len(begins) > 1 or len(ends) > 1:
        raise MarkerError("the eval:begin or eval:end marker appears more than once")
    begin, end = begins[0], ends[0]
    if end < begin.end():
        raise MarkerError("the eval:end marker comes before eval:begin")
    nl = line_ending(doc)
    lines = block.replace("\r\n", "\n").strip("\n").split("\n")
    return f"{doc[: begin.end()]}{nl}{nl.join(lines)}{nl}{doc[end:]}"


# ---------------------------------------------------------------- render


def _pct(value: Any) -> str:
    return "–" if not isinstance(value, int | float) else f"{100 * value:.0f} %"


def _num(value: Any, digits: int = 1) -> str:
    return "–" if not isinstance(value, int | float) else f"{value:.{digits}f}"


def _delta(value: Any, *, percent: bool) -> str:
    if not isinstance(value, int | float):
        return "–"
    if percent:
        return f"{100 * value:+.0f} pp"
    return f"{value:+.1f}"


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def _config_rows(configs: Sequence[Mapping[str, Any]]) -> list[str]:
    header = [
        "Configuration",
        "Valid (no repair)",
        "Merchant",
        "Date",
        "Total",
        "Items P / R / F1",
        "Name key",
        "Out of scope rejected",
        "False rejection",
        "Injection followed",
        "Invented examples",
        "Example amount on a real item",
        "Latency p50 / p95 (s)",
        "Tokens",
        "Missing",
    ]
    rows = []
    for config in configs:
        m = config["metrics"]
        at = partial(get_path, m)
        rows.append(
            [
                f"`{config['label']}`",
                f"{_pct(at('schema_validity', 'rate'))} "
                f"({_pct(at('schema_validity', 'rate_without_repair'))})",
                _pct(at("fields", "stored", "merchant")),
                _pct(at("fields", "stored", "date")),
                _pct(at("fields", "stored", "total")),
                " / ".join(
                    _pct(at("items", "stored", key)) for key in ("precision", "recall", "f1")
                ),
                _pct(at("name_key_agreement", "rate")),
                f"{at('out_of_scope', 'rejected')}/{at('out_of_scope', 'cases')}"
                f" (model {at('out_of_scope', 'by_model')}, "
                f"rule {at('out_of_scope', 'by_plausibility')})",
                f"{at('false_rejection', 'rejected')}/{at('false_rejection', 'cases')}",
                f"{at('injection_followed', 'followed')}/{at('injection_followed', 'cases')}",
                f"{at('invented_examples', 'items')} in {at('invented_examples', 'cases')}",
                f"{at('example_amount_on_real_item', 'items')} in "
                f"{at('example_amount_on_real_item', 'cases')}",
                f"{_num(at('latency_s', 'p50'))} / {_num(at('latency_s', 'p95'))}",
                _num(m.get("completion_tokens_mean"), 0),
                str(m.get("missing", 0)),
            ]
        )
    return _table(header, rows)


_COMPARED = (
    ("fields.stored.merchant", "Merchant", True),
    ("fields.stored.date", "Date", True),
    ("fields.stored.total", "Total", True),
    ("items.stored.f1", "Item F1", True),
    ("schema_validity.rate_without_repair", "Valid without repair", True),
    ("false_rejection.rate", "False rejection", True),
    ("invented_examples.items", "Invented examples", False),
    ("example_amount_on_real_item.items", "Example amount on a real item", False),
    ("latency_s.p50", "Latency p50 (s)", False),
)


def _comparison_rows(comparisons: Sequence[Mapping[str, Any]]) -> list[str]:
    header = ["b vs a", *(name for _, name, _ in _COMPARED), "Per case: wins / losses / ties"]
    rows = []
    for comparison in comparisons:
        deltas = comparison["deltas"]
        per_case = "; ".join(
            f"{name} {len(entry['wins'])}/{len(entry['losses'])}/{entry['ties']}"
            for name, entry in comparison["cases"].items()
        )
        rows.append(
            [
                f"`{comparison['b']}` vs `{comparison['a']}`",
                *(_delta(deltas.get(key), percent=percent) for key, _, percent in _COMPARED),
                per_case,
            ]
        )
    return _table(header, rows)


def _needs_review_tables(configs: Sequence[Mapping[str, Any]]) -> list[str]:
    lines: list[str] = []
    header = ["Configuration", "Status", "Truth", "TP", "FP", "FN", "TN", "Precision", "Recall"]
    rows = []
    for config in configs:
        review = config["metrics"].get("needs_review", {})
        for status, status_name in (
            ("status_app", "as shipped"),
            ("status_extraction", "extraction flags"),
        ):
            for truth, truth_name in (("derived", "derived"), ("hand", "hand label")):
                c = get_path(review, status, truth) or {}
                rows.append(
                    [
                        f"`{config['label']}`",
                        status_name,
                        truth_name,
                        *(str(c.get(key, "–")) for key in ("tp", "fp", "fn", "tn")),
                        _pct(c.get("precision")),
                        _pct(c.get("recall")),
                    ]
                )
    lines += _table(header, rows)
    return lines


def _error_rows(configs: Sequence[Mapping[str, Any]]) -> list[str]:
    header = ["Configuration", "Error category", "Count", "Example case"]
    rows = []
    for config in configs:
        errors = config["metrics"].get("errors", {})
        for name, entry in list(errors.items())[:TOP_ERRORS]:
            rows.append([f"`{config['label']}`", name, str(entry["count"]), entry["first_case"]])
    return _table(header, rows) if rows else ["No errors."]


def _outcome_grid(summary: Mapping[str, Any]) -> list[str]:
    labels = [config["label"] for config in summary.get("configs", [])]
    grid = summary.get("outcomes", {})
    if not grid:
        return ["No cases scored."]
    rows = [[case, *(grid[case].get(name, "–") for name in labels)] for case in sorted(grid)]
    return _table(["Case", *(f"`{name}`" for name in labels)], rows)


def _partial_banner(summary: Mapping[str, Any]) -> list[str]:
    """The partial-results banner with the reasons stored in the summary."""
    if not summary.get("partial"):
        return []
    reasons = summary.get("partial_reasons") or ["reason not recorded"]
    return [f"**Partial results:** {'; '.join(reasons)} (column Missing).", ""]


def render(summary: Mapping[str, Any]) -> str:
    """The generated block as markdown. Deterministic for the same summary."""
    configs = summary.get("configs", [])
    lines = [
        f"_Generated from `eval/results/summary.json`: today = {summary.get('today')}, "
        f"{summary.get('runs')} run(s) per case. Stored values (after the app's "
        "post-extraction step) are scored; rates count a failed receipt as wrong._",
        "",
        "### Configurations",
        "",
        *_partial_banner(summary),
        *_config_rows(configs),
        "",
        "Valid (no repair): the share of answers that passed the schema, and in brackets "
        "those that needed no repair turn. For a model `not_a_receipt` the extractor doesn't "
        "say whether a repair happened, so it counts as valid without repair. Missing: case "
        "runs without a matching cache entry, including infrastructure errors "
        "(`llm_*`, `interrupted`), which are never cached.",
        "",
        "Invented examples: an item equal to a prompt example (name and amount) that has no "
        "similar line on the receipt; the model added it. Example amount on a real item: an "
        "item equal to a prompt example whose name matches a real line with another amount. "
        "That can be a correct reading when the receipt prints the same price (e.g. the "
        "per-bottle Pfand 0.25), so each one needs a look at the photo.",
        "",
        "### Comparisons",
        "",
        *(
            _comparison_rows(summary.get("comparisons", []))
            if summary.get("comparisons")
            else ["No comparison: fewer than two of its configurations were scored."]
        ),
        "",
        "### needs_review against the truth",
        "",
        "Flagged = `needs_review` or `rejected`, among receipts that produced an expense. "
        "Derived truth: the stored extraction is wrong (merchant, date, total, or items not "
        "exact). Hand label: `expected_status` is `needs_review`.",
        "",
        *_needs_review_tables(configs),
        "",
        "### Top error categories",
        "",
        *_error_rows(configs),
        "",
        "### Outcome per case (run 1)",
        "",
        "Status of the stored expense, or the failure code; `*` = wrong by the derived truth.",
        "",
        *_outcome_grid(summary),
    ]
    return "\n".join(lines)

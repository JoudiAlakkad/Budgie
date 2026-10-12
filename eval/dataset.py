"""The ground truth in `data/eval/` and its validation (docs/wiki/backend/evaluation.md).

`<root>/cases/cNN.json`, one per case, and `<root>/images/` with the photos. Every problem
is reported as `cNN.field: reason`, all at once, and never quotes a value: the ground
truth is personal data. A warning (the items don't add up to the total) doesn't stop a
run.
"""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

CaseKind = Literal["receipt", "injection", "non_receipt"]
ExpectedStatus = Literal["accepted", "needs_review", "not_a_receipt"]
InjectedKey = Literal["merchant", "date", "total", "is_receipt"]

CASE_ID = re.compile(r"c\d{2}")
CURRENCY = re.compile(r"[A-Z]{3}")
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
NULL_WORDS = frozenset({"null", "none", ""})
CENT = Decimal("0.01")
SUM_WARNING = Decimal("0.02")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GroundTruthItem(_Strict):
    description: str
    amount: Decimal


class Case(_Strict):
    id: str
    image: str
    kind: CaseKind
    merchant: str | None
    date: date | None
    currency: str | None
    total: Decimal | None
    items: list[GroundTruthItem]
    expected_status: ExpectedStatus
    injected: dict[InjectedKey, str | float | bool] | None = None
    notes: str | None = None


class InvalidDataset(Exception):
    """The ground truth has problems; `problems` lists each as `cNN.field: reason`."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"{len(problems)} problem(s) in the ground truth")


@dataclass
class DatasetCheck:
    cases: list[Case] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not a JSON number")


def _read(path: Path) -> Any:
    # Decimals keep the amounts exact, so "at most 2 decimals" can be checked.
    return json.loads(
        path.read_text(encoding="utf-8"),
        parse_float=Decimal,
        parse_constant=_reject_constant,
    )


def _amount_problem(value: Any, *, nullable: bool) -> str | None:
    if value is None:
        return None if nullable else "must be a number"
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        return "must be a JSON number"
    amount = Decimal(value)
    if not amount.is_finite():
        return "must be finite"
    try:
        if amount != amount.quantize(CENT):
            return "has more than 2 decimals"
    except InvalidOperation:
        return "is out of range"
    return None


def _raw_problems(data: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """Problems pydantic wouldn't see or would coerce away: `(field, reason)`."""
    if "date" in data:
        value = data["date"]
        if isinstance(value, str) and value.strip().lower() in NULL_WORDS:
            yield "date", "use JSON null"
        elif value is not None and not isinstance(value, str):
            yield "date", "must be an ISO date string or null"
    if "total" in data and (reason := _amount_problem(data["total"], nullable=True)):
        yield "total", reason
    items = data.get("items")
    if isinstance(items, list):
        for index, item in enumerate(items):
            if isinstance(item, dict) and "amount" in item:
                reason = _amount_problem(item["amount"], nullable=False)
                if reason:
                    yield f"items[{index}].amount", reason
    currency = data.get("currency")
    if currency is not None and not (isinstance(currency, str) and CURRENCY.fullmatch(currency)):
        yield "currency", "must be 3 uppercase letters or null"
    injected = data.get("injected")
    if isinstance(injected, dict):
        yield from _injected_problems(injected)


def _injected_problems(injected: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """Each injected value has its field's type, so scoring never meets a surprise."""
    if "total" in injected and (reason := _amount_problem(injected["total"], nullable=False)):
        yield "injected.total", reason
    if "date" in injected:
        value = injected["date"]
        try:
            if not isinstance(value, str):
                raise ValueError
            date.fromisoformat(value)
        except ValueError:
            yield "injected.date", "must be an ISO date string"
    # Only `false`: the injected text asks the model to deny the receipt; `true` is what
    # a receipt is anyway, so following it couldn't be told apart.
    if "is_receipt" in injected and injected["is_receipt"] is not False:
        yield "injected.is_receipt", "must be false"
    if "merchant" in injected and not isinstance(injected["merchant"], str):
        yield "injected.merchant", "must be a string"


def _for_pydantic(data: dict[str, Any]) -> dict[str, Any]:
    """The injected values as `str | float | bool` (JSON numbers were read as Decimal)."""
    injected = data.get("injected")
    if isinstance(injected, dict):
        data = {
            **data,
            "injected": {
                key: float(value) if isinstance(value, Decimal) else value
                for key, value in injected.items()
            },
        }
    return data


def _location(loc: tuple[int | str, ...]) -> str:
    text = ""
    for part in loc:
        text += f"[{part}]" if isinstance(part, int) else (f".{part}" if text else str(part))
    return text or "(case)"


def _image_problems(name: str, images: Path) -> Iterator[str]:
    if "/" in name or "\\" in name or ".." in name or not name.strip():
        yield "must be a bare file name in images/"
        return
    if Path(name).suffix.lower() not in IMAGE_SUFFIXES:
        yield "must be a jpg, jpeg, png or webp file"
    if not (images / name).is_file():
        yield "file not found in images/"


def _rule_problems(case: Case) -> Iterator[tuple[str, str]]:
    if case.kind == "non_receipt":
        for name in ("merchant", "date", "total"):
            if getattr(case, name) is not None:
                yield name, "must be null for a non_receipt"
        if case.items:
            yield "items", "must be empty for a non_receipt"
        if case.expected_status != "not_a_receipt":
            yield "expected_status", "must be not_a_receipt for a non_receipt"
    else:
        if case.expected_status not in ("accepted", "needs_review"):
            yield "expected_status", f"must be accepted or needs_review for a {case.kind}"
        if case.total is None:
            yield "total", f"must not be null for a {case.kind}"
    if case.injected is not None and case.kind != "injection":
        yield "injected", "only allowed on an injection case"
    if case.kind == "injection" and not case.injected:
        yield "injected", "an injection case needs the values the injected text asks for"


def check_dataset(root: Path | str) -> DatasetCheck:
    """Read and validate every case; collects all problems and warnings."""
    root = Path(root)
    result = DatasetCheck()
    cases_dir, images = root / "cases", root / "images"
    if not cases_dir.is_dir():
        result.problems.append("cases: directory not found")
        return result
    if not images.is_dir():
        result.problems.append("images: directory not found")
    for path in sorted(cases_dir.glob("*.json")):
        label = path.stem
        problems: list[str] = []
        try:
            data = _read(path)
        except (OSError, ValueError) as exc:
            reason = "not valid JSON" if not isinstance(exc, OSError) else "can't be read"
            result.problems.append(f"{label}: {reason}")
            continue
        if not isinstance(data, dict):
            result.problems.append(f"{label}: must be a JSON object")
            continue
        problems += [f"{label}.{name}: {reason}" for name, reason in _raw_problems(data)]
        case_id = data.get("id")
        if isinstance(case_id, str):
            if not CASE_ID.fullmatch(case_id):
                problems.append(f"{label}.id: must look like c01")
            elif case_id != path.stem:
                # Equal to its unique file name, so ids are unique too.
                problems.append(f"{label}.id: must equal the file name")
        image = data.get("image")
        if isinstance(image, str):
            problems += [f"{label}.image: {r}" for r in _image_problems(image, images)]
        try:
            case = Case.model_validate(_for_pydantic(data))
        except ValidationError as exc:
            raw_fields = {p.split(": ", 1)[0] for p in problems}
            for error in exc.errors(include_url=False, include_input=False):
                where = f"{label}.{_location(error['loc'])}"
                # One reason per field: a raw-value problem already explains it.
                if not any(where == f or where.startswith((f"{f}.", f"{f}[")) for f in raw_fields):
                    problems.append(f"{where}: {error['msg'].lower()}")
            result.problems += problems
            continue
        problems += [f"{label}.{name}: {reason}" for name, reason in _rule_problems(case)]
        result.problems += problems
        if problems:
            continue
        result.cases.append(case)
        if case.total is not None and case.items:
            items_sum = sum((item.amount for item in case.items), Decimal(0))
            if abs(items_sum - case.total) > SUM_WARNING:
                result.warnings.append(f"{label}.items: the amounts don't add up to the total")
    if not result.cases and not result.problems:
        result.problems.append("cases: no case files found")
    return result


def load_dataset(root: Path | str) -> list[Case]:
    """Every case, sorted by id; raises `InvalidDataset` listing every problem."""
    check = check_dataset(root)
    if check.problems:
        raise InvalidDataset(check.problems)
    return sorted(check.cases, key=lambda case: case.id)

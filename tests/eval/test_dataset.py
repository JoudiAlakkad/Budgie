"""Ground-truth validation (`eval/dataset.py`): every problem as `cNN.field: reason`."""

import json
from pathlib import Path
from typing import Any

import pytest

from eval.dataset import InvalidDataset, check_dataset, load_dataset
from tests.eval.conftest import DATASET, case_data, png, write_case

SECRET_MERCHANT = "Wahrheitsladen Sonderbar"


def problems_for(tmp_path: Path, data: dict[str, Any], *, image: bool = True) -> list[str]:
    write_case(tmp_path, data, image=png((1, 2, 3)) if image else None, stem="c01")
    return check_dataset(tmp_path).problems


def test_the_synthetic_dataset_is_valid(dataset: Path) -> None:
    cases = load_dataset(dataset)

    assert [case.id for case in cases] == sorted(DATASET)
    assert cases[2].injected == {"total": 0.0}
    assert check_dataset(dataset).warnings == []


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"date": "null"}, "c01.date: use JSON null"),
        ({"date": "None"}, "c01.date: use JSON null"),
        ({"date": ""}, "c01.date: use JSON null"),
        ({"date": 20261001}, "c01.date: must be an ISO date string or null"),
        ({"date": "01.10.2026"}, "c01.date: "),
        ({"total": "3.68"}, "c01.total: must be a JSON number"),
        ({"total": 3.685}, "c01.total: has more than 2 decimals"),
        ({"total": True}, "c01.total: must be a JSON number"),
        (
            {"items": [{"description": "BROT", "amount": "2.49"}]},
            "c01.items[0].amount: must be a JSON number",
        ),
        (
            {"items": [{"description": "BROT", "amount": 0.001}]},
            "c01.items[0].amount: has more than 2 decimals",
        ),
        ({"currency": "eur"}, "c01.currency: must be 3 uppercase letters or null"),
        ({"currency": "EURO"}, "c01.currency: must be 3 uppercase letters or null"),
        ({"id": "c1"}, "c01.id: must look like c01"),
        ({"id": "c02"}, "c01.id: must equal the file name"),
        ({"image": "../c01.png"}, "c01.image: must be a bare file name in images/"),
        ({"image": "sub/c01.png"}, "c01.image: must be a bare file name in images/"),
        ({"image": "c01.gif"}, "c01.image: must be a jpg, jpeg, png or webp file"),
        ({"kind": "photo"}, "c01.kind: "),
        ({"expected_status": "rejected"}, "c01.expected_status: "),
        ({"surprise": 1}, "c01.surprise: extra inputs are not permitted"),
        ({"total": None}, "c01.total: must not be null for a receipt"),
        ({"expected_status": "not_a_receipt"}, "c01.expected_status: must be accepted or"),
        ({"injected": {"total": 0}}, "c01.injected: only allowed on an injection case"),
        ({"injected": {"shop": "x"}, "kind": "injection"}, "c01.injected.shop"),
        (
            {"injected": {"total": "zero"}, "kind": "injection"},
            "c01.injected.total: must be a JSON number",
        ),
        (
            {"injected": {"total": 0.001}, "kind": "injection"},
            "c01.injected.total: has more than 2 decimals",
        ),
        (
            {"injected": {"total": None}, "kind": "injection"},
            "c01.injected.total: must be a number",
        ),
        (
            {"injected": {"total": True}, "kind": "injection"},
            "c01.injected.total: must be a JSON number",
        ),
        (
            {"injected": {"date": "01.01.2020"}, "kind": "injection"},
            "c01.injected.date: must be an ISO date string",
        ),
        (
            {"injected": {"date": 20200101}, "kind": "injection"},
            "c01.injected.date: must be an ISO date string",
        ),
        (
            {"injected": {"is_receipt": "false"}, "kind": "injection"},
            "c01.injected.is_receipt: must be true or false",
        ),
        (
            {"injected": {"merchant": 7}, "kind": "injection"},
            "c01.injected.merchant: must be a string",
        ),
        ({"kind": "non_receipt"}, "c01.merchant: must be null for a non_receipt"),
        ({"kind": "non_receipt"}, "c01.items: must be empty for a non_receipt"),
        ({"kind": "non_receipt"}, "c01.expected_status: must be not_a_receipt for a non_receipt"),
    ],
)
def test_a_problem_is_reported_by_case_and_field(
    tmp_path: Path, fields: dict[str, Any], expected: str
) -> None:
    data = case_data("c01", merchant=SECRET_MERCHANT, **fields)
    problems = problems_for(tmp_path, data)

    assert any(problem.startswith(expected) for problem in problems), problems
    # Problems never quote a value: the ground truth is personal data.
    assert not any(SECRET_MERCHANT in problem for problem in problems)


def test_valid_injected_values_pass_and_get_one_reason_each_when_wrong(tmp_path: Path) -> None:
    good = {"total": 0, "date": "2020-01-01", "is_receipt": False, "merchant": "Hacker"}
    assert problems_for(tmp_path, case_data("c01", kind="injection", injected=good)) == []

    bad = {"total": None, "date": None}
    problems = problems_for(tmp_path, case_data("c01", kind="injection", injected=bad))
    assert problems == [
        "c01.injected.total: must be a number",
        "c01.injected.date: must be an ISO date string",
    ]


def test_the_null_date_gets_one_reason_not_two(tmp_path: Path) -> None:
    problems = problems_for(tmp_path, case_data("c01", date="null"))

    assert problems == ["c01.date: use JSON null"]


def test_a_missing_image_is_reported(tmp_path: Path) -> None:
    problems = problems_for(tmp_path, case_data("c01"), image=False)

    assert problems == ["c01.image: file not found in images/"]


def test_image_suffixes_are_case_insensitive(tmp_path: Path) -> None:
    write_case(tmp_path, case_data("c01", image="c01.JPG"), image=png((0, 0, 0)))

    assert check_dataset(tmp_path).problems == []


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_amounts_are_rejected(tmp_path: Path, constant: str) -> None:
    write_case(tmp_path, case_data("c01"), image=png((0, 0, 0)))
    path = tmp_path / "cases" / "c01.json"
    path.write_text(path.read_text().replace("3.68", constant), encoding="utf-8")

    assert check_dataset(tmp_path).problems == ["c01: not valid JSON"]


def test_every_problem_is_listed_at_once(tmp_path: Path) -> None:
    write_case(tmp_path, case_data("c01", date="null", currency="eur"), image=png((0, 0, 0)))
    write_case(tmp_path, case_data("c02", total=1.234), image=png((0, 0, 1)))

    with pytest.raises(InvalidDataset) as caught:
        load_dataset(tmp_path)

    assert caught.value.problems == [
        "c01.date: use JSON null",
        "c01.currency: must be 3 uppercase letters or null",
        "c02.total: has more than 2 decimals",
    ]


def test_an_item_sum_off_the_total_is_a_warning_not_an_error(tmp_path: Path) -> None:
    write_case(tmp_path, case_data("c01", total=9.99), image=png((0, 0, 0)))

    check = check_dataset(tmp_path)

    assert check.problems == []
    assert check.warnings == ["c01.items: the amounts don't add up to the total"]
    assert [case.id for case in load_dataset(tmp_path)] == ["c01"]


def test_a_sum_within_two_cents_is_no_warning(tmp_path: Path) -> None:
    write_case(tmp_path, case_data("c01", total=3.70), image=png((0, 0, 0)))

    assert check_dataset(tmp_path).warnings == []


def test_a_missing_cases_directory_is_a_problem(tmp_path: Path) -> None:
    assert check_dataset(tmp_path).problems == ["cases: directory not found"]


def test_an_empty_dataset_is_a_problem(tmp_path: Path) -> None:
    (tmp_path / "cases").mkdir()
    (tmp_path / "images").mkdir()

    assert check_dataset(tmp_path).problems == ["cases: no case files found"]


def test_a_file_that_is_not_an_object_is_a_problem(tmp_path: Path) -> None:
    (tmp_path / "cases").mkdir()
    (tmp_path / "images").mkdir()
    (tmp_path / "cases" / "c01.json").write_text(json.dumps([1, 2]), encoding="utf-8")

    assert check_dataset(tmp_path).problems == ["c01: must be a JSON object"]

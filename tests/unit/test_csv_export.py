"""`domain/csv_export.py`: the CSV export's format (contracts/csv-export.md; decision 0024)."""

import csv
import datetime as dt
import io
import re
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.csv_export import (
    COLUMNS,
    GUARDED_COLUMNS,
    ExportRow,
    export_filename,
    format_money,
    format_qty,
    guard,
    render,
    row_values,
)

CSV_EXPORT_MD = Path(__file__).resolve().parents[2] / "docs/wiki/contracts/csv-export.md"
# | `expense_id` | int | `42` |: the first cell of every row of the column table.
COLUMN_ROW = re.compile(r"^\|\s*`([a-z_]+)`\s*\|")

ROW = ExportRow(
    expense_id=42,
    date=dt.date(2026, 10, 3),
    merchant="REWE",
    currency="EUR",
    expense_total=Decimal("23.47"),
    position=0,
    item_description="BIO BANANE 1 KG",
    item_normalized_name="banane",
    item_qty=Decimal("1"),
    item_unit="kg",
    item_amount=Decimal("1.99"),
    category="groceries.fresh",
    source="ai_corrected",
)


def parse(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text, newline="")))


def fields(row: ExportRow) -> dict[str, str]:
    return dict(zip(COLUMNS, row_values(row), strict=True))


# ---------------------------------------------------------------- the contract's column table


def documented_columns() -> list[str]:
    text = CSV_EXPORT_MD.read_text(encoding="utf-8")
    return [m.group(1) for line in text.splitlines() if (m := COLUMN_ROW.match(line))]


def test_header_row_equals_the_contract_column_table_in_order() -> None:
    documented = documented_columns()

    assert len(documented) == 12, "the parser found the column table"
    assert parse(render([]))[0] == documented
    assert list(COLUMNS) == documented


def test_guarded_columns_are_string_columns_of_the_table() -> None:
    assert {"merchant", "item_description", "item_normalized_name", "item_unit"} == GUARDED_COLUMNS
    assert set(COLUMNS) >= GUARDED_COLUMNS


# ---------------------------------------------------------------- values


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("1.99"), "1.99"),
        (Decimal("2"), "2.00"),
        (Decimal("2.5"), "2.50"),
        (Decimal("-0.25"), "-0.25"),
        (Decimal("-3"), "-3.00"),
        (Decimal("0"), "0.00"),
        (Decimal("-0.00"), "0.00"),
        (Decimal("0.005"), "0.01"),
        (Decimal("-0.005"), "-0.01"),
        (Decimal("1E+3"), "1000.00"),
        (Decimal("9999999999.99"), "9999999999.99"),
        (None, ""),
    ],
)
def test_money_has_exactly_two_decimals(value: Decimal | None, expected: str) -> None:
    assert format_money(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("1"), "1"),
        (Decimal("1.000"), "1"),
        (Decimal("1.234"), "1.234"),
        (Decimal("1.2340"), "1.234"),
        (Decimal("0.5"), "0.5"),
        (Decimal("100"), "100"),
        (Decimal("1E+2"), "100"),
        (Decimal("1.5E-7"), "0.00000015"),
        (Decimal("0"), "0"),
        (Decimal("0E-7"), "0"),
        (Decimal("-2"), "-2"),
        (None, ""),
    ],
)
def test_qty_is_plain_without_exponent_or_trailing_zeros(
    value: Decimal | None, expected: str
) -> None:
    assert format_qty(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=SUM(A1:A2)", "'=SUM(A1:A2)"),
        ("+49 Pfand", "'+49 Pfand"),
        ("-20% Rabatt", "'-20% Rabatt"),
        ("@cmd", "'@cmd"),
        ("\tTAB", "'\tTAB"),
        ("\rCR", "'\rCR"),
        ("REWE", "REWE"),
        ("A=B", "A=B"),
        (" =x", " =x"),
        ("'t Hoekje", "''t Hoekje"),
        ("", ""),
        (None, ""),
    ],
)
def test_guard(value: str | None, expected: str) -> None:
    assert guard(value) == expected


@pytest.mark.parametrize("column", sorted(GUARDED_COLUMNS))
@pytest.mark.parametrize("value", ["=1+1", "+1", "-1", "@x", "\tx", "\rx"])
def test_every_string_column_is_guarded(column: str, value: str) -> None:
    row = replace(ROW, **{column: value})

    assert fields(row)[column] == "'" + value


def test_negative_amounts_are_never_prefixed() -> None:
    row = replace(
        ROW,
        expense_total=Decimal("-0.25"),
        item_amount=Decimal("-0.25"),
        item_qty=Decimal("-1"),
        category="discount",
    )

    values = fields(row)

    assert values["expense_total"] == "-0.25"
    assert values["item_amount"] == "-0.25"
    assert values["item_qty"] == "-1"
    assert parse(render([row]))[1][COLUMNS.index("item_amount")] == "-0.25"


def test_row_values_in_column_order() -> None:
    assert row_values(ROW) == (
        "42",
        "2026-10-03",
        "REWE",
        "EUR",
        "23.47",
        "BIO BANANE 1 KG",
        "banane",
        "1",
        "kg",
        "1.99",
        "groceries.fresh",
        "ai_corrected",
    )


def test_null_is_an_empty_field() -> None:
    row = replace(ROW, item_qty=None, item_unit=None)

    assert render([row]).splitlines()[1] == (
        "42,2026-10-03,REWE,EUR,23.47,BIO BANANE 1 KG,banane,,,1.99,groceries.fresh,ai_corrected"
    )


# ---------------------------------------------------------------- file format


def test_crlf_line_ends_and_header_only_for_no_rows() -> None:
    assert render([]) == ",".join(COLUMNS) + "\r\n"
    text = render([ROW, replace(ROW, position=1)])

    assert text.count("\r\n") == 3
    assert "\n" not in text.replace("\r\n", "")
    assert text.endswith("\r\n")


@pytest.mark.parametrize(
    ("description", "line_field"),
    [
        ("plain", "plain"),
        ("a, b", '"a, b"'),
        ('12" Pizza', '"12"" Pizza"'),
        ("two\nlines", '"two\nlines"'),
        ("two\r\nlines", '"two\r\nlines"'),
        (" spaced ", " spaced "),
    ],
)
def test_quoting_is_minimal(description: str, line_field: str) -> None:
    row = replace(ROW, item_description=description)
    text = render([row])

    assert f",{line_field},banane," in text
    assert parse(text)[1][COLUMNS.index("item_description")] == description


def test_umlauts_round_trip_through_utf8() -> None:
    row = replace(ROW, merchant="Bäckerei Müller", item_description="BRÖTCHEN ß", item_unit="Stück")

    data = render([row]).encode("utf-8")

    assert not data.startswith(b"\xef\xbb\xbf"), "no BOM"
    parsed = parse(data.decode("utf-8"))[1]
    assert parsed[COLUMNS.index("merchant")] == "Bäckerei Müller"
    assert parsed[COLUMNS.index("item_description")] == "BRÖTCHEN ß"
    assert parsed[COLUMNS.index("item_unit")] == "Stück"


def test_rows_sorted_by_date_then_expense_id_then_position() -> None:
    day1, day2 = dt.date(2026, 10, 1), dt.date(2026, 10, 2)
    rows = [
        replace(ROW, expense_id=1, date=day2, position=0, item_description="d"),
        replace(ROW, expense_id=9, date=day1, position=1, item_description="c"),
        replace(ROW, expense_id=9, date=day1, position=0, item_description="b"),
        replace(ROW, expense_id=3, date=day1, position=2, item_description="a"),
    ]

    parsed = parse(render(rows))[1:]

    assert [line[COLUMNS.index("item_description")] for line in parsed] == ["a", "b", "c", "d"]


def test_a_missing_date_sorts_last_and_is_empty() -> None:
    rows = [replace(ROW, expense_id=1, date=None), replace(ROW, expense_id=2)]

    parsed = parse(render(rows))[1:]

    assert [(line[0], line[1]) for line in parsed] == [("2", "2026-10-03"), ("1", "")]


# ---------------------------------------------------------------- filename


@pytest.mark.parametrize(
    ("date_from", "date_to", "expected"),
    [
        (None, None, "budgie-expenses_all_all.csv"),
        (dt.date(2026, 10, 1), None, "budgie-expenses_2026-10-01_all.csv"),
        (None, dt.date(2026, 10, 31), "budgie-expenses_all_2026-10-31.csv"),
        (dt.date(2026, 10, 1), dt.date(2026, 10, 31), "budgie-expenses_2026-10-01_2026-10-31.csv"),
        (dt.date(1, 1, 1), dt.date(9999, 12, 31), "budgie-expenses_0001-01-01_9999-12-31.csv"),
    ],
)
def test_export_filename(date_from: dt.date | None, date_to: dt.date | None, expected: str) -> None:
    assert export_filename(date_from, date_to) == expected

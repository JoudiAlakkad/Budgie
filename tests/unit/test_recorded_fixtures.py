"""Every committed recorded response has the fixture shape and no personal data (0017)."""

import json
from pathlib import Path

import pytest

from app.domain.redaction import PLACEHOLDERS
from tests.unit.fixture_shape import (
    KNOWN_PLACEHOLDERS,
    assert_fixture_shape,
    personal_data,
    suspicious,
    suspicious_texts,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "recorded_responses"
FILES = sorted(FIXTURES.glob("*.json")) if FIXTURES.is_dir() else []

pytestmark = pytest.mark.skipif(
    not FIXTURES.is_dir(),
    reason="tests/fixtures/recorded_responses/ not generated yet (scripts/make_fixtures.py)",
)


def test_folder_has_fixtures() -> None:
    assert FILES


@pytest.mark.parametrize("path", FILES, ids=[path.name for path in FILES])
def test_fixture_shape(path: Path) -> None:
    assert_fixture_shape(path.stem, json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("path", FILES, ids=[path.name for path in FILES])
def test_fixture_has_no_personal_data(path: Path) -> None:
    assert personal_data(json.loads(path.read_text(encoding="utf-8"))) == []


@pytest.mark.parametrize("path", FILES, ids=[path.name for path in FILES])
def test_fixture_passes_the_independent_scan(path: Path) -> None:
    # Only pattern names are reported: the text itself may be personal data.
    assert suspicious_texts(json.loads(path.read_text(encoding="utf-8"))) == []


def test_known_placeholders_match_the_redaction() -> None:
    assert set(KNOWN_PLACEHOLDERS) == PLACEHOLDERS


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("Terminal 55501234", ["5+ digits"]),
        ("info@example.de", ["at sign"]),
        ("Karte ****4242", ["masked digits"]),
        ("XXXX XXXX 1234", ["masked digits"]),
        ("Musterstr. [address]", ["street"]),
        ("Hauptstraße", ["street"]),
        ("Tel. 0231 98", ["contact or tax label with a value"]),
        ("USt-IdNr.: DE12", ["contact or tax label with a value"]),
        ('{"total": 123456}', ["5+ digits"]),
    ],
)
def test_independent_scan_flags(text: str, found: list[str]) -> None:
    assert suspicious(text) == found


@pytest.mark.parametrize(
    "text",
    [
        "Tel. [phone]",
        "USt-IdNr.: [taxid]",
        "Fax",
        "Karte [card]",
        '{"total": 7.39, "date": "2026-09-17"}',
        "17.09.2026 14:32:05",
        "SUMME EUR 1.234,56",
        "[address]\n[address]",
        "Bon [id]",
    ],
)
def test_independent_scan_allows(text: str) -> None:
    assert suspicious(text) == []

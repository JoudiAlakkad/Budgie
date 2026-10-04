"""Every committed recorded response has the fixture shape and no personal data (0017)."""

import json
from pathlib import Path

import pytest

from tests.unit.fixture_shape import assert_fixture_shape, personal_data

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

"""scripts/make_demo_receipts.py: the committed demo receipts for scenario 1 (F12)."""

from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image

from app.domain.categorize import normalize
from app.services.item_categories import load_seed
from tests.unit.fixture_shape import load_script

demo = load_script("make_demo_receipts")
COMMITTED = Path(__file__).resolve().parents[2] / "data" / "demo" / "receipts"


@pytest.mark.parametrize(
    ("amount", "text"),
    [(Decimal("1.5"), "1,50"), (Decimal("13.24"), "13,24"), (Decimal("0.25"), "0,25")],
)
def test_money_is_german_format(amount: Decimal, text: str) -> None:
    assert demo.money(amount) == text


def test_only_the_mismatch_receipt_disagrees_with_its_items() -> None:
    mismatched = {r.file for r in demo.RECEIPTS if r.item_sum != r.printed_total}
    assert mismatched == {"demo-03-kiosk-total-mismatch.png"}
    kiosk = next(r for r in demo.RECEIPTS if r.file in mismatched)
    assert kiosk.printed_total - kiosk.item_sum == Decimal("1.00")


def test_text_is_ascii_because_the_bundled_font_has_no_umlauts() -> None:
    for receipt in demo.RECEIPTS:
        for left, right in demo.lines(receipt):
            assert (left + right).isascii(), left


def test_each_receipt_has_an_item_the_seed_table_does_not_know() -> None:
    """The demo should show the uncategorised flow, so every shop needs one unknown name."""
    seed = load_seed()
    for receipt in demo.RECEIPTS:
        names = {normalize(name).name for name, _ in receipt.items}
        assert names - set(seed), receipt.file


def test_render_is_deterministic(tmp_path: Path) -> None:
    first = demo.write_all(tmp_path / "a")
    second = demo.write_all(tmp_path / "b")
    assert [p.read_bytes() for p in first] == [p.read_bytes() for p in second]


def test_committed_receipts_exist_and_open() -> None:
    for receipt in demo.RECEIPTS:
        path = COMMITTED / receipt.file
        with Image.open(path) as image:
            assert image.format == "PNG"
            assert image.width == demo.WIDTH
            assert image.height == 2 * demo.MARGIN + demo.LINE * len(demo.lines(receipt))

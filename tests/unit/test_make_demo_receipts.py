"""scripts/make_demo_receipts.py: the committed demo receipts for scenario 1 (F12)."""

import tomllib
from decimal import Decimal
from pathlib import Path

import PIL
import pytest
from PIL import Image, ImageChops, features

from app.domain.categorize import normalize
from app.services.item_categories import load_seed
from tests.unit.fixture_shape import load_script

demo = load_script("make_demo_receipts")
ROOT = Path(__file__).resolve().parents[2]
COMMITTED = ROOT / "data" / "demo" / "receipts"


def _pinned_pillow() -> str:
    """The exact Pillow version in the `eval` extra, the one the demo PNGs are rendered with."""
    with (ROOT / "backend" / "pyproject.toml").open("rb") as file:
        extra = tomllib.load(file)["project"]["optional-dependencies"]["eval"]
    pins = [req.split("==", 1)[1].strip() for req in extra if req.lower().startswith("pillow==")]
    assert len(pins) == 1, f"the eval extra must pin Pillow exactly (pillow==X.Y.Z): {extra}"
    return pins[0]


PINNED_PILLOW = _pinned_pillow()


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


def _unknown_names(receipt) -> set[str]:
    seed = load_seed()
    return {normalize(name).name for name, _ in receipt.items} - set(seed)


def _receipt(prefix: str):
    return next(r for r in demo.RECEIPTS if r.file.startswith(prefix))


@pytest.mark.parametrize("prefix", ["demo-01", "demo-02"])
def test_supermarket_and_cafe_each_have_an_item_the_seed_table_does_not_know(
    prefix: str,
) -> None:
    """The demo shows the uncategorised flow on the first two receipts."""
    assert _unknown_names(_receipt(prefix))


def test_cafe_items_the_seed_knows_are_all_eating_out() -> None:
    """A cafe purchase must not land under groceries; only the unknown item is left open."""
    seed = load_seed()
    names = {normalize(name).name for name, _ in _receipt("demo-02").items}
    assert {seed[n] for n in names if n in seed} == {"eating_out"}


def test_kiosk_unknowns_all_appear_on_the_supermarket_receipt() -> None:
    """After the choice on demo-01, demo-03 is fully categorised: it shows reuse, no new pick."""
    supermarket = {normalize(name).name for name, _ in _receipt("demo-01").items}
    assert _unknown_names(_receipt("demo-03")) <= supermarket


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


def test_installed_pillow_is_the_pinned_one() -> None:
    """A drifted Pillow shows up here as a version problem, not as a pixel diff below."""
    assert PIL.__version__ == PINNED_PILLOW, (
        f"Pillow {PIL.__version__} is installed, but backend/pyproject.toml pins "
        f"{PINNED_PILLOW} for the pixel-exact demo check: run `make install`"
    )


@pytest.mark.parametrize("receipt", demo.RECEIPTS, ids=lambda r: r.file)
def test_committed_receipts_match_the_script_pixel_for_pixel(receipt) -> None:
    """An item edit that keeps the line count must not leave a stale PNG behind."""
    path = COMMITTED / receipt.file
    with Image.open(path) as image:
        committed = image.convert("RGB")
    rendered = demo.render(receipt)
    assert rendered.size == committed.size, f"{path.name} is stale: run `make demo-receipts`"
    diff = ImageChops.difference(rendered, committed).getbbox()
    versions = f"Pillow {PIL.__version__}, FreeType {features.version('freetype2')}"
    assert diff is None, (
        f"{path.name} differs from the script in {diff} (installed: {versions}): "
        f"run `make demo-receipts` with the pinned Pillow {PINNED_PILLOW}"
    )

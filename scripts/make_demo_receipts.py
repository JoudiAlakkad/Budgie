"""Render the synthetic demo receipts for scenario 1 into data/demo/receipts/.

`python scripts/make_demo_receipts.py [--out DIR]` (needs Pillow, the `eval` extra).

The receipts are invented: fictional shops, no address, no card or customer data, so they
can be committed (the eval photos can't, decision 0028). Pillow's bundled font has no
umlauts or euro sign, so the text is ASCII, as on many German till printers.
"""

import argparse
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "demo" / "receipts"
WIDTH = 640
MARGIN = 40
FONT_SIZE = 26
LINE = 38
FOOTER = "Synthetischer Demo-Beleg"


@dataclass(frozen=True)
class DemoReceipt:
    file: str
    merchant: str
    printed_date: str  # as printed: DD.MM.YYYY HH:MM
    items: tuple[tuple[str, Decimal], ...]
    printed_total: Decimal  # differs from the item sum only where a flag is the point
    purpose: str  # what the receipt shows in the demo (README)

    @property
    def item_sum(self) -> Decimal:
        return sum((amount for _, amount in self.items), Decimal("0"))


RECEIPTS: tuple[DemoReceipt, ...] = (
    DemoReceipt(
        file="demo-01-supermarket.png",
        merchant="SPERLING MARKT",
        printed_date="16.10.2026 17:42",
        items=(
            ("BIO BANANE 1 KG", Decimal("1.99")),
            ("H-MILCH 3,5%", Decimal("1.09")),
            ("VOLLKORNBROT", Decimal("2.49")),
            ("BUTTER", Decimal("2.29")),
            ("COLA 1,5L", Decimal("1.79")),
            ("PFAND", Decimal("0.25")),
            ("SPUELMITTEL", Decimal("1.45")),
            ("CHIPS PAPRIKA", Decimal("1.89")),
        ),
        printed_total=Decimal("13.24"),
        purpose="seed categories plus one uncategorised item (chips paprika) and a Pfand line",
    ),
    DemoReceipt(
        file="demo-02-cafe.png",
        merchant="CAFE AMSEL",
        printed_date="17.10.2026 10:15",
        items=(
            ("CAPPUCCINO", Decimal("3.40")),
            ("LATTE MACCHIATO", Decimal("3.60")),
            ("KAESEKUCHEN", Decimal("3.90")),
        ),
        printed_total=Decimal("10.90"),
        purpose="all eating out, with one uncategorised item (kaesekuchen)",
    ),
    DemoReceipt(
        file="demo-03-kiosk-total-mismatch.png",
        merchant="KIOSK FINK",
        printed_date="18.10.2026 21:03",
        items=(
            ("BIER", Decimal("1.20")),
            ("BIER", Decimal("1.20")),
            ("CHIPS PAPRIKA", Decimal("1.89")),
        ),
        printed_total=Decimal("5.29"),
        purpose="no new unknown item: chips paprika again, so the category chosen on demo-01 is"
        " reused; the printed total is 1.00 more than the items, so the review shows a sum flag",
    ),
)


def money(amount: Decimal) -> str:
    """German format with two decimals: 1.5 -> '1,50'."""
    return f"{amount:.2f}".replace(".", ",")


def lines(receipt: DemoReceipt) -> list[tuple[str, str]]:
    """The printed lines as (left, right) text; right is empty for plain lines."""
    out = [(receipt.merchant, ""), (receipt.printed_date, ""), ("", ""), ("", "EUR")]
    out += [(name, money(amount)) for name, amount in receipt.items]
    out += [("-" * 30, ""), ("SUMME", money(receipt.printed_total)), ("", ""), (FOOTER, "")]
    return out


def render(receipt: DemoReceipt) -> Image.Image:
    """The receipt as a white RGB image with black text."""
    rows = lines(receipt)
    image = Image.new("RGB", (WIDTH, 2 * MARGIN + LINE * len(rows)), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=FONT_SIZE)
    for i, (left, right) in enumerate(rows):
        y = MARGIN + i * LINE
        draw.text((MARGIN, y), left, fill="black", font=font)
        if right:
            draw.text((WIDTH - MARGIN, y), right, fill="black", font=font, anchor="ra")
    return image


def write_all(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for receipt in RECEIPTS:
        path = out_dir / receipt.file
        render(receipt).save(path, format="PNG", optimize=True)
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT_DIR, help=f"default: {OUT_DIR}")
    args = parser.parse_args(argv)
    for path in write_all(args.out):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

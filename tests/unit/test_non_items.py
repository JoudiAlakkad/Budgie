"""The non-item guard and the deposit-return words (decisions 0026, 0027;
docs/wiki/backend/domain-logic.md#non_itemspy-f13). One table per function."""

import re
import time
from decimal import Decimal

import pytest

from app.domain import non_items
from app.domain.categorize import MAX_DESCRIPTION_CHARS
from app.domain.non_items import (
    AMOUNT_ONLY_WORDS,
    DEPOSIT_RETURN_WORDS,
    NON_ITEM_WORDS,
    ItemLine,
    folded,
    has_letter,
    is_deposit_line,
    is_deposit_return,
    is_non_item,
    is_non_item_line,
    is_quantity_line,
    merge_quantity_lines,
    paid_total,
    quantity_unit,
)

# --- is_non_item ----------------------------------------------------------------------

DROPPED = [
    "SUMME",
    "Summe EUR",
    "SUMME:",
    "  summe   15,15",
    "ZU ZAHLEN",
    "zu  zahlen",
    "Zu zahlen EUR 15,15",
    "GESAMT",
    "Gesamt 12,40",
    "GESAMTSUMME",
    "Zwischensumme 9,99",
    "EC-Kartenzahlung",
    "EC KARTE 5,67",
    "EC-Karten-Zahlung",
    "ec zahlung",
    "Wechselgeld 4,83",
    "Gesamtbetrag 15,15",
    "TOTAL",
    "Total EUR 15,15",
    "EC-Cash",
    "Kreditkarte",
    "Zw.-Summe 9,99",
    "Zw-Summe",
    "Netto EUR 12,62",
    "VISA ****1234",
    "Mastercard kontaktlos",
    "A 19 %",
    "A 19,0% 1,66 0,32",
    "B 7% 0,93 0,07 1,00",
    "SUMME LEERGUT",  # a total of returns: the returns are items already
    "Gesamt Leergut 0,50-",
    "BAR",
    "Bar EUR",
    "BAR-Zahlung",
    "GEGEBEN",
    "Rückgeld",
    "RUECKGELD",
    "ZURÜCK",
    "Zurück 4,83",
    "EC-Karte",
    "EC-KARTE girocard",
    "Kartenzahlung",
    "girocard",
    "VISA",
    "Visa contactless",
    "Mastercard",
    "MwSt 19%",
    "MWST.",
    "USt 7 %",
    "Netto",
    "NETTO 12,62",
    "Netto 12,62 €",
    "Brutto",
    "Brutto: 15,15",
    "Brutto 19% A 15,02",
    "Netto A 12,62",
    "BAR 20,00 EUR",
    "Bar Euro",
    "Steuer",
    "Steuer A 19%",
    # no letter at all (0027)
    "219,000",
    "4711",
    "-",
    "12,99 €",
    "0,25-",
    "%",
    "  19 %  ",
]

KEPT = [
    "Pfand zurück",
    "PFAND ZURUECK",
    "Leergut",
    "Pfand",
    "BARILLA SPAGHETTI",
    "Barbecue Sauce",
    "Bärlauch",
    "Steuerberater-Kalender",
    "Visavis Kekse",
    "Summer Rolls",
    "Gesamtschule Heft",
    "Netzbeutel",
    "Ustersbacher Bier",
    "Bruttoeinkommen Ratgeber",
    "NETTO Spülmittel",  # Netto is also a shop name
    "Bar-Riegel",  # so is Bar a product word
    "BAR NUSS SCHOKO",
    "Bar Eis",
    "BAR-B-Q Sauce",
    "Bar Mix Cocktail",
    "VISA Geschenkkarte 50",
    "Mastercard Prepaid",
    "Steuer-Spar-Erklaerung 2026",
    "Total Cola",
    "A 4 Hefte",  # a class letter and a number, but no rate
    "Netto Apfel",
    "Netto Marken-Discount Kaffee",
    "Brutto Ratgeber",
    "Mastercardhalter",
    "Zurück Leergut",  # a deposit return is never a non-item
    "Zurück Pfand 0,25",
    "BIO BANANE 1 KG",
    "Schokolade Zartbitter",
    "Sauce zu Zahlen",  # a word not at the start
    # blank: kept, so the user sees the amount and names it (0027)
    "",
    "   ",
    # quantity lines are merged, not dropped, even without a letter (0027)
    "3 × 0,89",
    "24 x 0,49 €",
    "-53 x 0,25 €",
    "Ä 1",  # one letter is enough
    "Молоко 1,09",  # so is another script
]


@pytest.mark.parametrize("description", DROPPED)
def test_non_items_are_recognised(description: str) -> None:
    assert is_non_item(description)


@pytest.mark.parametrize("description", KEPT)
def test_real_items_are_kept(description: str) -> None:
    assert not is_non_item(description)


def test_every_listed_word_alone_is_a_non_item() -> None:
    for word in NON_ITEM_WORDS:
        assert is_non_item(word), word
        assert is_non_item(word.upper()), word
        assert not is_non_item(f"{word}x"), word  # whole words only
    for word in AMOUNT_ONLY_WORDS:
        assert is_non_item(word), word
        assert is_non_item(f"{word.upper()} 1,23"), word
        assert not is_non_item(f"{word} Kaffee"), word
        assert not is_non_item(f"{word}x 1,23"), word


# --- is_deposit_return ----------------------------------------------------------------

RETURNS = [
    "LEERGUT",
    "Leergut 3,00",
    "Leergut-Bon",
    "LEERGUTBON",
    "Pfandrückgabe",
    "PFANDRUECKGABE",
    "Pfandbon",
    "PFANDBON 1,50",
    "Pfand zurück",
    "pfand  zurueck",
    "EINWEG LEERGUT",
    "Leergutrückgabe",
    "Flaschenrückgabe 2,00",
    "Flaschen Rückgabe 3,00",
    "LEERGUTRÜCKNAHME",
    "LEERGUTRÜCKNAME 19%",  # the model's misspelling of the ALDI line
    "Pfandrücknahme",
    "Flaschen-Rückgabe",
    "Zurück Pfand",
    "zurück pfand 0,25",
]

NOT_RETURNS = [
    "Pfand",
    "PFAND 0,25",
    "Einwegpfand",
    "Mehrweg",
    "Pfand -0,25",  # a plain Pfand is never a return word, whatever its sign
    "Leergutautomat Reiniger",  # not a whole word
    "Rueckgaberecht Ratgeber",
    "Pfandbonus",
    "Zurück",
    "MINERALWASSER",
    "",
    # a bare Rückgabe is no return word (0027): ALDI prints its Pfand charge so
    "Rückgabe",
    "Pfand / Rückgabe",
    "PFAND / RÜCKGABE 0,25",
    "Pfand-Rückgabe",
]


@pytest.mark.parametrize("description", RETURNS)
def test_deposit_returns_are_recognised(description: str) -> None:
    assert is_deposit_return(description)


@pytest.mark.parametrize("description", NOT_RETURNS)
def test_other_lines_are_not_deposit_returns(description: str) -> None:
    assert not is_deposit_return(description)


def test_every_listed_return_word_is_one() -> None:
    for word in DEPOSIT_RETURN_WORDS:
        assert is_deposit_return(word), word
        assert is_deposit_return(f"x {word.upper()} 1,50"), word


# --- folding --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("  RÜCKGELD  ", "rueckgeld"),
        ("Pfand\t\nzurück", "pfand zurueck"),
        ("Straße", "strasse"),
        ("Café", "cafe"),
        ("", ""),
    ],
)
def test_folded(text: str, expected: str) -> None:
    assert folded(text) == expected


def test_only_the_first_characters_are_read() -> None:
    long = "x" * MAX_DESCRIPTION_CHARS + " Leergut"

    assert not is_deposit_return(long)
    assert len(folded("a" * 10 * MAX_DESCRIPTION_CHARS)) == MAX_DESCRIPTION_CHARS


# --- slow-regex guard (workflow.md, CI checks) ----------------------------------------

SLOW_S = 2.0

HOSTILE_UNITS = [
    "a",
    " ",
    "\t",
    "-",
    "pfand ",
    "pfand",
    "leergut",
    "leergu",
    "rueck",
    "ü",
    "zu ",
    "summe",
    "bar ",
    "ec-",
    "1,",
    "1 x ",
    "x",
    "1,0 kg x 1,0 ",
    "-1",
    "€/kg",
    "zu zahlen ",
]


def hostile_runs(size: int) -> list[str]:
    runs = [(unit * (size // len(unit) + 1))[:size] for unit in HOSTILE_UNITS]
    line = "SUMME 15,15 BAR 20,00 Pfand zurück Leergut-Bon MwSt 19% "
    runs.append((line * (size // len(line) + 1))[:size])
    return runs


@pytest.mark.parametrize("size", [MAX_DESCRIPTION_CHARS, 65_536])
def test_the_guard_finishes_on_hostile_input(size: int) -> None:
    for text in hostile_runs(size):
        start = time.perf_counter()
        is_non_item(text)
        is_deposit_return(text)
        is_quantity_line(text)
        is_deposit_line(text)
        merge_quantity_lines([ItemLine(text, Decimal(1)), ItemLine(text, Decimal(1))])
        paid_total([ItemLine(text, Decimal(1))], None)
        elapsed = time.perf_counter() - start
        assert elapsed < SLOW_S, (text[:20], elapsed)


def test_every_regex_finishes_on_hostile_input_of_the_bounded_size() -> None:
    patterns = [value for value in vars(non_items).values() if isinstance(value, re.Pattern)]
    assert len(patterns) == 7
    for pattern in patterns:
        for text in hostile_runs(MAX_DESCRIPTION_CHARS):
            start = time.perf_counter()
            for _ in pattern.finditer(text):
                pass
            elapsed = time.perf_counter() - start
            assert elapsed < SLOW_S, (pattern.pattern, text[:20], elapsed)


# --- no letter (0027) -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [("219,000", False), ("€ × % -", False), ("", False), ("a", True), ("ß", True), ("é", True)],
)
def test_has_letter(text: str, expected: bool) -> None:
    assert has_letter(text) is expected


# --- is_quantity_line (0027) ----------------------------------------------------------

D = Decimal

QUANTITY_LINES = [
    ("24 x 0,49 €", (D("24"), D("0.49"))),
    ("3 x 0,89 €", (D("3"), D("0.89"))),
    ("3 x 0,89", (D("3"), D("0.89"))),
    ("3 × 0,89", (D("3"), D("0.89"))),
    ("2x0,95", (D("2"), D("0.95"))),
    ("2 X 0.95 EUR", (D("2"), D("0.95"))),
    ("2 x 0,49 € A", (D("2"), D("0.49"))),  # a tax class copied after the price
    ("2 x 0,49 B", (D("2"), D("0.49"))),
    ("3 x 0,89 1", (D("3"), D("0.89"))),
    ("2 Stück x 0,49", (D("2"), D("0.49"))),
    ("1,066 kg x 0,99 €/kg", (D("1.066"), D("0.99"))),
    ("0,512 KG X 2,99 EUR/KG", (D("0.512"), D("2.99"))),
    ("500 g x 1,20 €/kg", (D("500"), D("1.20"))),
    ("2 Stk x 1,50 €/Stk", (D("2"), D("1.50"))),
    ("2 St. x 1,50", (D("2"), D("1.50"))),
    ("1,5 l x 0,99", (D("1.5"), D("0.99"))),
    ("-53 x 0,25 €", (D("-53"), D("0.25"))),
    ("−53 x 0,25 €", (D("-53"), D("0.25"))),
    ("53 x -0,25 €", (D("53"), D("-0.25"))),
    ("+2 x 0,95", (D("2"), D("0.95"))),
    ("  24  x  0,49  €  ", (D("24"), D("0.49"))),
]

NOT_QUANTITY_LINES = [
    "BROT",
    "2 x",
    "x 0,49",
    "0,49",
    "219,000",
    "24 x 0,49 € WASSER",
    "WASSER 24 x 0,49 €",
    "0 x 0,49",
    "2 x 3 x 0,49",
    "2 kgx",
    "1234567 x 0,49",
    "",
]


@pytest.mark.parametrize(("text", "expected"), QUANTITY_LINES)
def test_quantity_lines_are_read(text: str, expected: tuple[Decimal, Decimal]) -> None:
    assert is_quantity_line(text) == expected


@pytest.mark.parametrize("text", NOT_QUANTITY_LINES)
def test_other_lines_are_not_quantity_lines(text: str) -> None:
    assert is_quantity_line(text) is None


# --- is_deposit_line ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("PFAND", True),
        ("PFAND / RÜCKGABE 0,25", True),
        ("Einwegpfand", True),
        ("LEERGUTRÜCKNAME 19%", True),
        ("Flaschenrückgabe", True),  # a return word without a deposit marker
        ("Pfand zurück", True),
        ("MINERALWASSER", False),
        ("Dampfandruck", False),
        ("", False),
    ],
)
def test_deposit_lines(text: str, expected: bool) -> None:
    assert is_deposit_line(text) is expected


# --- merge_quantity_lines (0027) ------------------------------------------------------


def line(description: str, amount: str, qty: str | None = None) -> ItemLine:
    return ItemLine(description, Decimal(amount), None if qty is None else Decimal(qty))


MERGES = [
    pytest.param(
        [line("24 x 0,49 €", "11.76", "24"), line("WASSER", "0.25", "2")],
        [line("WASSER", "11.76", "24")],
        1,
        id="before: the line's amount when it is N x price",
    ),
    pytest.param(
        [line("RINDERHACK", "4.17", "1"), line("3 x 1,39 €", "4.17")],
        [line("RINDERHACK", "4.17", "3")],
        1,
        id="after: the previous item when there is no next one",
    ),
    pytest.param(
        [line("BANANEN", "1.06"), line("1,066 kg x 0,99 €/kg", "1.06"), line("BROT", "2.49")],
        [ItemLine("BANANEN", Decimal("1.06"), Decimal("1.066"), "kg"), line("BROT", "2.49")],
        1,
        id="between: the previous item wins when its amount is N x price",
    ),
    pytest.param(
        [line("RINDERHACK", "4.17"), line("3 x 1,39 €", "4.17"), line("BANANEN", "1.06")],
        [line("RINDERHACK", "4.17", "3"), line("BANANEN", "1.06")],
        1,
        id="after, another product next: no double count",
    ),
    pytest.param(
        [line("BROT", "2.49"), line("2 x 0,95", "1.90"), line("BROETCHEN", "0.95")],
        [line("BROT", "2.49"), line("BROETCHEN", "1.90", "2")],
        1,
        id="previous amount differs: the next item, as before",
    ),
    pytest.param(
        [line("2 x 0,95", "5.00"), line("BROETCHEN", "1.90")],
        [line("BROETCHEN", "1.90", "2")],
        1,
        id="mismatched amount: the neighbour keeps its own",
    ),
    pytest.param(
        [line("2 x 0,95", "1.92"), line("BROETCHEN", "0.95")],
        [line("BROETCHEN", "1.92", "2")],
        1,
        id="within 0.02: the line's amount",
    ),
    pytest.param(
        [line("2 x 0,95", "1.93"), line("BROETCHEN", "0.95")],
        [line("BROETCHEN", "0.95", "2")],
        1,
        id="0.03 off: the neighbour's amount",
    ),
    pytest.param(
        [line("PFAND", "0.25"), line("3 x 0,89 €", "2.67", "3"), line("PFAND", "0.75")],
        [line("PFAND", "0.25"), line("3 x 0,89 €", "2.67", "3"), line("PFAND", "0.75")],
        0,
        id="deposit neighbours on both sides: the line stays",
    ),
    pytest.param(
        [line("3 x 0,89 €", "2.67"), line("PFAND", "0.75"), line("BROT", "2.49")],
        [line("3 x 0,89 €", "2.67"), line("PFAND", "0.75"), line("BROT", "2.49")],
        0,
        id="only a deposit next: never the item after it",
    ),
    pytest.param(
        [line("BROT", "2.49"), line("3 x 0,89 €", "2.67"), line("PFAND", "0.75")],
        [line("BROT", "2.49"), line("3 x 0,89 €", "2.67"), line("PFAND", "0.75")],
        0,
        id="deposit next, previous is another product: kept, BROT keeps 2.49",
    ),
    pytest.param(
        [line("-53 x 0,25 €", "-13.25"), line("LEERGUT", "0.99")],
        [line("LEERGUT", "-13.25", "53")],
        1,
        id="negative: may merge into a deposit",
    ),
    pytest.param(
        [line("53 x -0,25 €", "-13.25"), line("Pfandrückgabe", "-13.25")],
        [line("Pfandrückgabe", "-13.25", "53")],
        1,
        id="negative price: may merge into a deposit",
    ),
    pytest.param(
        [line("-53 x 0,25 €", "13.25"), line("LEERGUT", "-13.25")],
        [line("LEERGUT", "-13.25", "53")],
        1,
        id="negative with the sign lost: the neighbour's amount",
    ),
    pytest.param(
        [line("LEERGUT", "0.25"), line("-53 x 0,25 €", "-13.25"), line("CREMA", "2.29")],
        [line("LEERGUT", "-13.25", "53"), line("CREMA", "2.29")],
        1,
        id="negative: never into a product, so the deposit before it",
    ),
    pytest.param(
        [line("-53 x 0,25 €", "-13.25"), line("CREMA", "2.29")],
        [line("-53 x 0,25 €", "-13.25"), line("CREMA", "2.29")],
        0,
        id="negative without a deposit neighbour: kept as is",
    ),
    pytest.param(
        [line("24 x 0,49 €", "11.76")],
        [line("24 x 0,49 €", "11.76")],
        0,
        id="no neighbour: kept as is",
    ),
    pytest.param(
        [line("2 x 0,95", "1.90"), line("3 x 1,00", "3.00"), line("BROT", "2.49")],
        [line("2 x 0,95", "1.90"), line("BROT", "3.00", "3")],
        1,
        id="two in a row: the first has no qualifying neighbour",
    ),
    pytest.param(
        [line("RINDERHACK", "1.39"), line("3 x 1,39 €", "4.17"), line("PFAND", "0.25")],
        [line("RINDERHACK", "4.17", "3"), line("PFAND", "0.25")],
        1,
        id="previous amount is the unit price: the previous item",
    ),
    pytest.param(
        [line("MILCH", "1.19"), line("53 x 0,25 €", "13.25"), line("LEERGUT", "13.25")],
        [line("MILCH", "1.19"), line("53 x 0,25 €", "13.25"), line("LEERGUT", "13.25")],
        0,
        id="sign lost before a deposit: MILCH is not overwritten",
    ),
    pytest.param(
        [line("2 x 0,95", "1.90"), line("3 x 0,89", "2.67"), line("BROT", "2.67")],
        [line("2 x 0,95", "1.90"), line("BROT", "2.67", "3")],
        1,
        id="next already a quantity line: the first stays",
    ),
    pytest.param(
        [line("MILCH", "1.90"), line("2 x 0,95", "1.90"), line("3 x 1,00", "3.00")],
        [line("MILCH", "1.90", "2"), line("3 x 1,00", "3.00")],
        1,
        id="two in a row after: the item absorbs only one",
    ),
    pytest.param(
        [line("2 x 0,95", "1.90"), line("BROT", "2.49"), line("3 x 1,00", "3.00")],
        [line("BROT", "1.90", "2"), line("3 x 1,00", "3.00")],
        1,
        id="a neighbour absorbs at most one line",
    ),
    pytest.param(
        [
            line("2 x 0,95", "1.90"),
            line("BROT", "2.49"),
            line("3 x 1,00", "3.00"),
            line("EI", "1"),
        ],
        [line("BROT", "1.90", "2"), line("EI", "3.00", "3")],
        2,
        id="each line its own neighbour",
    ),
    pytest.param(
        [line("2 x 0,95", "1.90"), line("   ", "1.90"), line("SUMME", "1.90")],
        [line("2 x 0,95", "1.90"), line("   ", "1.90"), line("SUMME", "1.90")],
        0,
        id="neighbours without a name or that are non-items don't qualify",
    ),
    pytest.param([], [], 0, id="no items"),
]


@pytest.mark.parametrize(("lines", "expected", "count"), MERGES)
def test_merge_quantity_lines(lines: list[ItemLine], expected: list[ItemLine], count: int) -> None:
    assert merge_quantity_lines(lines) == (expected, count)


def test_merge_keeps_the_input_unchanged() -> None:
    lines = [line("24 x 0,49 €", "11.76"), line("WASSER", "0.25")]
    copy = list(lines)

    merge_quantity_lines(lines)

    assert lines == copy


# --- paid_total (0027) ----------------------------------------------------------------

PAID = [
    pytest.param([], D("219.00"), None, None, id="no paid line: keep"),
    pytest.param([line("SUMME", "54.40")], D("219.00"), None, None, id="summe doesn't count"),
    pytest.param([line("BAR", "54.40")], None, None, None, id="payment line doesn't count"),
    pytest.param(
        [line("ZU ZAHLEN", "54.4")],
        D("219.0"),
        D("54.40"),
        D("54.40"),
        id="one, differs, items match it",
    ),
    pytest.param([line("Gesamtbetrag 54,40", "54.40")], None, D("54.40"), None, id="total null"),
    pytest.param([line("ZU ZAHLEN", "54.40")], D("54.40"), None, None, id="total equal"),
    pytest.param([line("ZU ZAHLEN", "15.17")], D("15.15"), None, None, id="within 0.02: keep"),
    pytest.param(
        [line("ZU ZAHLEN", "15.18")],
        D("15.15"),
        D("15.18"),
        D("15.18"),
        id="0.03 off, items match it",
    ),
    pytest.param(
        [line("ZU ZAHLEN", "54.40"), line("Gesamtbetrag", "54.41")],
        D("219.00"),
        D("54.40"),
        D("54.40"),
        id="two agreeing: the first",
    ),
    pytest.param(
        [line("ZU ZAHLEN", "54.40"), line("GESAMTBETRAG", "60.00")],
        D("219.00"),
        None,
        None,
        id="two disagreeing: keep",
    ),
    pytest.param(
        [line("ZU ZAHLEN", "54.40"), line("GESAMTBETRAG", "60.00")],
        None,
        None,
        None,
        id="two disagreeing, total null: stays null",
    ),
    pytest.param([line("zu zahlen", "1.005")], None, D("1.01"), None, id="rounded to cents"),
    pytest.param([line("zu zahlenx", "54.40")], None, None, None, id="whole words only"),
    pytest.param([line("ZU ZAHLEN", "0.0")], D("54.40"), None, None, id="zero is ignored"),
    pytest.param([line("ZU ZAHLEN", "0.0")], None, None, None, id="zero, total null"),
    pytest.param([line("ZU ZAHLEN", "-5.00")], None, None, None, id="negative ignored"),
    pytest.param(
        [line("ZU ZAHLEN", "219.00")], D("54.40"), None, D("54.40"), id="misread paid line"
    ),
    pytest.param([line("ZU ZAHLEN", "54.4")], D("219.0"), None, None, id="no items sum: keep"),
]


@pytest.mark.parametrize(("dropped", "total", "expected", "items_sum"), PAID)
def test_paid_total(
    dropped: list[ItemLine],
    total: Decimal | None,
    expected: Decimal | None,
    items_sum: Decimal | None,
) -> None:
    assert paid_total(dropped, total, items_sum) == expected


PAID_WITH_ITEMS = [
    # the model's total matches the kept items: a misread paid line doesn't replace it
    pytest.param("54.40", "54.40", "54.90", None, id="items match the model's total"),
    pytest.param("54.40", "54.41", "54.90", None, id="items within 0.02"),
    # the items don't match the model's total: the paid line wins (the ALDI case)
    pytest.param("219.00", "54.40", "54.40", Decimal("54.40"), id="items match the paid line"),
    pytest.param("219.00", "60.00", "54.40", None, id="items match neither: keep"),
    # no model total: the paid line, whatever the items say
    pytest.param(None, "54.40", "54.90", Decimal("54.90"), id="no model total"),
]


@pytest.mark.parametrize(("total", "items_sum", "paid", "expected"), PAID_WITH_ITEMS)
def test_paid_total_keeps_a_total_the_items_add_up_to(
    total: str | None, items_sum: str, paid: str, expected: Decimal | None
) -> None:
    dropped = [ItemLine("ZU ZAHLEN", Decimal(paid))]
    model_total = None if total is None else Decimal(total)

    assert paid_total(dropped, model_total, Decimal(items_sum)) == expected


NON_ITEM_LINES = [
    pytest.param(line("-0,50", "-0.50"), False, id="a discount without letters is kept"),
    pytest.param(line("-30%", "-0.60"), False, id="a percent discount is kept"),
    pytest.param(line("219,000", "219.00"), True, id="a positive number is dropped"),
    pytest.param(line("4711", "0"), True, id="zero counts as not negative"),
    pytest.param(line("SUMME", "-5.00"), True, id="a word line is dropped whatever its sign"),
    pytest.param(line("BROT", "2.49"), False, id="an item"),
]


@pytest.mark.parametrize(("item", "expected"), NON_ITEM_LINES)
def test_is_non_item_line(item: ItemLine, expected: bool) -> None:
    assert is_non_item_line(item) is expected


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("1,066 kg x 0,99 €/kg", "kg"),
        ("500 g x 1,20", "g"),
        ("2 l x 0,89", "l"),
        ("24 x 0,49 €", None),
        ("3 stk x 1,00", None),
        ("BROT", None),
    ],
)
def test_quantity_unit(description: str, expected: str | None) -> None:
    assert quantity_unit(description) == expected

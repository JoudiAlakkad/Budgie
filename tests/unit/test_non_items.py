"""The non-item guard and the deposit-return words (decision 0026;
docs/wiki/backend/domain-logic.md#non_itemspy-f13). One table per function."""

import re
import time

import pytest

from app.domain import non_items
from app.domain.categorize import MAX_DESCRIPTION_CHARS
from app.domain.non_items import (
    DEPOSIT_RETURN_WORDS,
    NON_ITEM_WORDS,
    TAX_AMOUNT_WORDS,
    folded,
    is_deposit_return,
    is_non_item,
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
    "Steuer",
    "Steuer A 19%",
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
    "Netto Marken-Discount Kaffee",
    "Brutto Ratgeber",
    "Mastercardhalter",
    "Zurück Leergut",  # a deposit return is never a non-item
    "BIO BANANE 1 KG",
    "Schokolade Zartbitter",
    "Sauce zu Zahlen",  # a word not at the start
    "",
    "   ",
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
    for word in TAX_AMOUNT_WORDS:
        assert is_non_item(word), word
        assert is_non_item(f"{word.upper()} 1,23"), word
        assert not is_non_item(f"{word} Kaffee"), word


# --- is_deposit_return ----------------------------------------------------------------

RETURNS = [
    "LEERGUT",
    "Leergut 3,00",
    "Leergut-Bon",
    "LEERGUTBON",
    "Pfandrückgabe",
    "PFANDRUECKGABE",
    "Pfand-Rückgabe",
    "Rückgabe",
    "Pfandbon",
    "PFANDBON 1,50",
    "Pfand zurück",
    "pfand  zurueck",
    "EINWEG LEERGUT",
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
        elapsed = time.perf_counter() - start
        assert elapsed < SLOW_S, (text[:20], elapsed)


def test_every_regex_finishes_on_hostile_input_of_the_bounded_size() -> None:
    patterns = [value for value in vars(non_items).values() if isinstance(value, re.Pattern)]
    assert len(patterns) == 3
    for pattern in patterns:
        for text in hostile_runs(MAX_DESCRIPTION_CHARS):
            start = time.perf_counter()
            for _ in pattern.finditer(text):
                pass
            elapsed = time.perf_counter() - start
            assert elapsed < SLOW_S, (pattern.pattern, text[:20], elapsed)

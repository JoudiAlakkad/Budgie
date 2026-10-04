"""Redaction rules (decision 0017): each rule, the must-survive negatives, idempotence,
and `clean_merchant`."""

import json
import subprocess
import sys
import time
from collections.abc import Callable

import pytest

from app.domain.redaction import (
    MAX_TEXT_CHARS,
    PLACEHOLDERS,
    RULES,
    TRUNCATED,
    Finding,
    clean_merchant,
    find_personal_data,
    iban_checksum_valid,
    luhn_valid,
    redact_text,
)

# (rule kind, input, expected output)
POSITIVES = [
    # the official examples, spaced and compact
    ("iban", "DE89 3704 0044 0532 0130 00", "[iban]"),
    ("iban", "IBAN: DE89370400440532013000", "IBAN: [iban]"),
    ("iban", "AT61 1904 3002 3457 3201", "[iban]"),
    ("iban", "AT611904300234573201", "[iban]"),
    ("iban", "CH93 0076 2011 6238 5295 7", "[iban]"),
    ("iban", "CH9300762011623852957", "[iban]"),
    ("iban", "NL91 ABNA 0417 1643 00", "[iban]"),
    ("iban", "NL91ABNA0417164300", "[iban]"),
    ("iban", "GB82 WEST 1234 5698 7654 32", "[iban]"),
    ("iban", "GB82WEST12345698765432", "[iban]"),
    ("iban", "FR14 2004 1010 0505 0001 3M02 606", "[iban]"),
    ("iban", "Lastschrift IBAN DE89370400440532013000 Mandat", "Lastschrift IBAN [iban] Mandat"),
    ("iban", "IBAN: DE89 3704 0044 0532 0130 00, BIC", "IBAN: [iban], BIC"),
    # a word after a spaced IBAN is not one of its groups
    ("iban", "AT61 1904 3002 3457 3201 EUR", "[iban] EUR"),
    ("iban", "AT61 1904 3002 3457 3201 BANK", "[iban] BANK"),
    ("iban", "AT61 1904 3002 3457 3201 BANKING", "[iban] BANKING"),
    # a rejected candidate right before it doesn't hide the IBAN
    ("iban", "XX12 DE89 3704 0044 0532 0130 00", "XX12 [iban]"),
    # masked IBANs on direct-debit receipts: no checksum to verify
    ("iban_masked", "DE89 **** **** **** **30 00", "[iban]"),
    ("iban_masked", "DE89XXXXXXXXXXXXXX3000", "[iban]"),
    ("iban_masked", "DE89 XXXX XXXX XXXX XXXX 00", "[iban]"),
    ("iban_masked", "DE89 3704 **** **** **30 00", "[iban]"),
    ("iban_masked", "DE89****3000", "[iban]"),
    ("iban_masked", "IBAN: DE89 #### #### #### ##30 00 Mandat", "IBAN: [iban] Mandat"),
    ("card", "4111 1111 1111 1111", "[card]"),
    ("card", "5500-0000-0000-0004", "[card]"),
    ("card", "Karte 4242424242424242 gelesen", "Karte [card] gelesen"),
    ("card_masked", "****1234", "[card]"),
    ("card_masked", "**** **** **** 1234", "[card]"),
    ("card_masked", "XXXX1234", "[card]"),
    ("card_masked", "XXXX XXXX 1234", "[card]"),
    ("card_masked", "#### 1234", "[card]"),
    ("card_masked", "xxxxxxxxxxxx1234", "[card]"),
    ("card_masked", "MAX ****1234", "MAX [card]"),
    ("labelled_id", "Terminal-ID: 12345678", "Terminal-ID: [id]"),
    ("labelled_id", "TID 87654321", "TID [id]"),
    ("labelled_id", "Trace-Nr. 004711", "Trace-Nr. [id]"),
    ("labelled_id", "TA-Nr.: 123456", "TA-Nr.: [id]"),
    ("labelled_id", "Beleg-Nr. 0815", "Beleg-Nr. [id]"),
    ("labelled_id", "BON-NR: 5678", "BON-NR: [id]"),
    ("labelled_id", "Kasse 3 Bon 1234", "Kasse [id] Bon [id]"),
    ("labelled_id", "Transaktion 4711", "Transaktion [id]"),
    ("labelled_id", "TSE-Seriennummer: 7f3a9c1e5d", "TSE-Seriennummer: [id]"),
    ("labelled_id", "TSE-Signatur: MEUCIQD1x8+abc==", "TSE-Signatur: [id]"),
    ("labelled_id", "Seriennr. AB12345", "Seriennr. [id]"),
    ("labelled_id", "Kunden-Nr. 99887766", "Kunden-Nr. [id]"),
    ("labelled_id", "Kartennummer: 12345678", "Kartennummer: [id]"),
    ("labelled_id", "AID: A0000000031010", "AID: [id]"),
    ("labelled_id", "Genehmigungs-Nr. 123456", "Genehmigungs-Nr. [id]"),
    ("labelled_id", "Autorisierung 654321", "Autorisierung [id]"),
    ("labelled_id", "VU-Nr. 4567891", "VU-Nr. [id]"),
    ("labelled_id", "Filiale 1234", "Filiale [id]"),
    ("labelled_id", "Fil.-Nr. 0042", "Fil.-Nr. [id]"),
    ("labelled_id", "Terminal-ID: 5317 4492", "Terminal-ID: [id]"),
    # a tab or no-break space between label and value
    ("labelled_id", "Kasse\t12", "Kasse\t[id]"),
    ("labelled_id", "Terminal-ID:\t55501234", "Terminal-ID:\t[id]"),
    ("labelled_id", "Kasse\u00a012", "Kasse\u00a0[id]"),
    # a hyphen right after a label, followed by a digit
    ("labelled_id", "TID-123", "TID-[id]"),
    ("labelled_id", "Kasse-3 Bon-Nr. 12", "Kasse-[id] Bon-Nr. [id]"),
    # a labelled id followed by a card number: the id is cut back to the part before the
    # card, so a second pass finds nothing new (test_idempotent)
    ("labelled_id", "Kunden-Nr. 123 4111111111111111", "Kunden-Nr. [id] [card]"),
    ("labelled_id", "Bon 12 4111 1111 1111 1111", "Bon [id] [card]"),
    ("labelled_id", "Kasse 3 4111111111111111 x", "Kasse [id] [card] x"),
    ("labelled_id", "AID A000 4111111111111111", "AID [id] [card]"),
    # the last digit group is a price or a quantity, not part of the id
    ("labelled_id", "Kasse 3 4,99", "Kasse [id] 4,99"),
    ("labelled_id", "Kasse 3 5 kg", "Kasse [id] 5 kg"),
]

NEGATIVES = [
    # prices
    "15,17",
    "15.17",
    "-0,50",
    "1.234,56",
    "€ 3,99",
    "3,99 A",
    "2 x 3,99",
    "SUMME EUR 15,17",
    "A 19,0% 12,74 2,42 15,16",
    # dates and times
    "2026-09-17",
    "17.09.2026",
    "17.09.26",
    "14:32",
    "14:32:05",
    "Datum: 17.09.2026 Uhrzeit: 14:32:05",
    "01-09-2026",
    "05/10/2026",
    "Datum 01/09/2026",
    "02-10-2026 14:32",
    # weights and quantities
    "0,456 kg",
    "1 KG",
    "12 STK.",
    "500G",
    "0,456 kg x 2,99 EUR/kg",
    # EAN-13 codes: the first three fail Luhn, the last passes Luhn but is a valid EAN
    "4006381333931",
    "4012345678901",
    "4061458022218",
    "5901234123457",
    # item names
    "BIO EIER OKT 12 STK.",
    "RISPENTOMATEN KG-WARE",
    "JOGHURT NACH GRIECH. A",
    "ZU ZAHLEN",
    "Kartenzahlung",
    "SUMME EUR",
    "Diesel-Schwefelarm addit.",
    "ZWIEBELRING 12 STK",
    "BEDIENUNGSTHEKE",
    "Kassenbon",
    "Hering 2",
    "APFELRING 2 x 1,99",
    "STREUSELRING 1 STK",
    "Holzweg 2 Stück",
    "KÖNIGSBERGER PLATZ 2 x 3,49",
    # item names ending in a street suffix and a count (an address rule used to take them)
    "APFELRING 2",
    "STREUSELRING 1",
    "Holzweg 2",
    "KÖNIGSBERGER PLATZ 2",
    # counts that look like a postcode and city
    "10000 BONUSPUNKTE",
    "12345 Meilen",
    "50000 Punkte",
    "25000 Mal",
    # chain names
    "ALDI SÜD",
    "Netto",
    "REWE",
    "dm-drogerie markt",
    "Action",
    "TEDi",
    # plain numbers
    "0815",
    "123456",
    "Kasse 14:32",
    # prices and quantities after an id label
    "Bon 2.49",
    "Bon 12.5",
    "Kasse 2,49",
    "Bon 1.234,56",
    "Filiale 2,5 kg",
    "Filiale 2 kg",
    "Seriennr. 2026-09-17",
    # units without a space, thousands, and a unit after a space
    "Bon 500g",
    "Kasse 2kg",
    "Kasse 3x",
    "Bon 3.500",
    "Kasse 5 kg",
    "Kasse 14:32:05",
    "Kasse -0,50",
    "Bon 01/09/2026",
    # a hyphen after a label, then a word: not a separator
    "Beleg-Kopie 2",
    "TID-ABC",
    "Kasse-A1",
    # a label inside a word
    "Kassenbon 1234",
    "BONBON 3",
    # uppercase item names shaped like the start of an IBAN
    "PC24 BLAUBEEREN",
    "XL12 HANDTUCH",
    "AB12 CDEF GHIJ",
    "AB12 CDEF GHIJ KLMN",
    "DE12 BIO MILCH",
    "XXL24 SHIRT",
    "GR12 TOMATEN 500G",
    # IBAN-shaped, but one digit off: the checksum fails
    "DE89 3704 0044 0532 0130 01",
    "DE89370400440532013001",
    "GB82 WEST 1234 5698 7654 33",
    # a valid checksum, but glued to a word or with two spaces between groups
    "DE89 3704 0044 0532 0130 00ABC",
    "DE89370400440532013000ABC",
    "DE89  3704  0044  0532  0130  00",
    # a valid DE IBAN cut short: the length doesn't match the country
    "DE89 3704 0044 0532",
    # masked, but nothing shown after the mask, or no mask at all
    "DE89 **** **** ****",
    "DE12 1234 5678 9012",
]

# Contact data the schema keeps out of every field but `merchant`, and `clean_merchant`
# cuts from there. `redact_text` no longer has rules for it, so it passes unchanged; the
# fixture generator's independent scan (tests/unit/fixture_shape.py) still flags it.
NO_LONGER_REDACTED = [
    # addresses
    "Hauptstraße 12",
    "Beispielstr. 12a",
    "Am Lindenplatz 3",
    "An der Ruhrallee 5",
    "Kölner Straße 12-14",
    "MUSTERWEG 7",
    "Musterstraße 12a, 44227 Dortmund",
    "Hauptstraße 12\n44227 Dortmund",
    '{"merchant": "Markt, Lindenweg 4"}',
    "44227 Dortmund",
    "80331 München",
    "01067 DRESDEN",
    # phone numbers
    "+49 231 123456",
    "0231/123456",
    "0231-12 34 56",
    "Tel. 0231 123456",
    "Fon: 0231 9876543",
    "Fax 0231 123457",
    "Telefon: 0231123456",
    # URLs
    "https://www.beispiel-markt.de/filialen",
    "www.example-shop.de",
    "Besuchen Sie example.de!",
    "service.com/hilfe",
]


@pytest.mark.parametrize(("kind", "text", "expected"), POSITIVES)
def test_rule_redacts(kind: str, text: str, expected: str) -> None:
    assert redact_text(text) == expected
    assert kind in {f.kind for f in find_personal_data(text)}


@pytest.mark.parametrize("text", NEGATIVES + NO_LONGER_REDACTED)
def test_must_survive(text: str) -> None:
    assert find_personal_data(text) == []
    assert redact_text(text) == text


def test_rule_table() -> None:
    assert [rule.kind for rule in RULES] == [
        "iban",
        "iban_masked",
        "card",
        "card_masked",
        "labelled_id",
    ]
    assert sorted(PLACEHOLDERS) == ["[card]", "[iban]", "[id]"]


@pytest.mark.parametrize(("kind", "text", "expected"), POSITIVES)
def test_idempotent(kind: str, text: str, expected: str) -> None:
    once = redact_text(text)
    assert redact_text(once) == once


def test_every_rule_kind_has_two_positives() -> None:
    kinds = [kind for kind, _, _ in POSITIVES]
    assert {rule.kind for rule in RULES} <= set(kinds)
    assert all(kinds.count(kind) >= 2 for kind in set(kinds))


@pytest.mark.parametrize("placeholder", sorted(PLACEHOLDERS))
def test_placeholders_match_no_rule(placeholder: str) -> None:
    assert '"' not in placeholder and "\\" not in placeholder
    assert find_personal_data(f"Kasse {placeholder} Terminal-ID: {placeholder}") == []


RECEIPT = """Beispiel Markt GmbH
Musterstraße 12a
44227 Dortmund
Tel. 0231 9876543
www.beispiel-markt.de
USt-IdNr.: DE999999999

BIO EIER OKT 10 STK.          2,99 A
VOLLMILCH 3,5%                1,19 A
2 x 0,79
JOGHURT NACH GRIECH. A        1,58 A
RISPENTOMATEN KG-WARE
0,456 kg x 2,99 EUR/kg        1,36 A
SUMME EUR                     7,12
Kartenzahlung                 7,12

Karte: **** **** **** 4242
Terminal-ID: 55501234
Trace-Nr. 004711
Datum: 17.09.2026 Uhrzeit: 14:32:05
Es bediente Sie: Erika
Kasse: 3 Bon 1234
Vielen Dank für Ihren Einkauf!"""

# The header stays: `redact_text` only covers what the schema lets through.
RECEIPT_REDACTED = """Beispiel Markt GmbH
Musterstraße 12a
44227 Dortmund
Tel. 0231 9876543
www.beispiel-markt.de
USt-IdNr.: DE999999999

BIO EIER OKT 10 STK.          2,99 A
VOLLMILCH 3,5%                1,19 A
2 x 0,79
JOGHURT NACH GRIECH. A        1,58 A
RISPENTOMATEN KG-WARE
0,456 kg x 2,99 EUR/kg        1,36 A
SUMME EUR                     7,12
Kartenzahlung                 7,12

Karte: [card]
Terminal-ID: [id]
Trace-Nr. [id]
Datum: 17.09.2026 Uhrzeit: 14:32:05
Es bediente Sie: Erika
Kasse: [id] Bon [id]
Vielen Dank für Ihren Einkauf!"""


def test_synthetic_receipt() -> None:
    assert redact_text(RECEIPT) == RECEIPT_REDACTED
    assert redact_text(RECEIPT_REDACTED) == RECEIPT_REDACTED


def test_json_stays_valid() -> None:
    data = {
        "is_receipt": True,
        "merchant": "Beispiel Märkte\nFiliale 1234",
        "date": "2026-09-17",
        "line_items": [
            {"description": "Karte ****4242", "qty": None, "unit_price": None, "amount": 7.12},
            {"description": 'Gutschein "Bon 4711"', "qty": 1, "unit_price": 0.5},
            {"description": "Terminal-ID:\t55501234", "qty": 1, "unit_price": 0.0},
        ],
        "total": 7.12,
        "payment_method": "card",
        "unreadable_fields": ["merchant"],
        "raw": RECEIPT,
    }
    for document in (json.dumps(data, indent=2), json.dumps(data, ensure_ascii=False)):
        redacted = json.loads(redact_text(document))
        assert redacted["merchant"] == "Beispiel Märkte\nFiliale [id]"
        assert redacted["line_items"][0]["description"] == "Karte [card]"
        assert redacted["line_items"][1]["description"] == 'Gutschein "Bon [id]"'
        assert redacted["line_items"][2]["description"] == "Terminal-ID:\t[id]"
        assert redacted["raw"] == RECEIPT_REDACTED
        assert redacted["date"] == "2026-09-17"
        assert redacted["total"] == 7.12


@pytest.mark.parametrize(
    ("digits", "valid"),
    [
        ("4111111111111111", True),
        ("5500000000000004", True),
        ("79927398713", True),
        ("4111111111111112", False),
        ("4006381333931", False),
        ("79927398710", False),
    ],
)
def test_luhn(digits: str, valid: bool) -> None:
    assert luhn_valid(digits) is valid


@pytest.mark.parametrize(
    ("iban", "valid"),
    [
        ("DE89370400440532013000", True),
        ("DE89 3704 0044 0532 0130 00", True),
        ("GB82WEST12345698765432", True),
        ("NL91ABNA0417164300", True),
        ("CH9300762011623852957", True),
        ("FR1420041010050500013M02606", True),
        ("DE89370400440532013001", False),
        ("DE88370400440532013000", False),
        ("GB82WEST12345698765433", False),
        ("PC24BLAUBEEREN", False),
        ("de89370400440532013000", False),
        ("DE89-3704-0044-0532-0130-00", False),
        ("DE89٣70400440532013000", False),  # a non-ASCII digit
        ("DE89", False),
        ("", False),
    ],
)
def test_iban_checksum(iban: str, valid: bool) -> None:
    assert iban_checksum_valid(iban) is valid


def test_iban_checksum_is_fast_on_a_long_input() -> None:
    # Digit by digit, no big integer: about a millisecond per 64k digits.
    assert seconds(iban_checksum_valid, "DE00" + "9" * MAX_TEXT_CHARS) < SLOW_S


def test_luhn_invalid_card_number_survives() -> None:
    assert redact_text("4111 1111 1111 1112") == "4111 1111 1111 1112"


def test_findings_sorted_and_not_overlapping() -> None:
    findings = find_personal_data(RECEIPT)
    assert findings == sorted(findings, key=lambda f: f.start)
    assert all(a.end <= b.start for a, b in zip(findings, findings[1:], strict=False))
    assert [f.kind for f in findings] == [
        "card_masked",
        "labelled_id",
        "labelled_id",
        "labelled_id",
        "labelled_id",
    ]


def test_earlier_rule_wins_on_overlap() -> None:
    # The labelled_id rule also matches the digits, but the card rule comes first.
    assert find_personal_data("Kartennummer: 4111 1111 1111 1111") == [Finding("card", 14, 33)]


def test_a_labelled_id_is_cut_back_to_an_earlier_finding() -> None:
    assert find_personal_data("Bon 12 4111 1111 1111 1111") == [
        Finding("labelled_id", 4, 6),
        Finding("card", 7, 26),
    ]
    # nothing is left before the card, so only the card is found
    assert find_personal_data("Bon 4111111111111111") == [Finding("card", 4, 20)]


def test_rules_without_fit_lose_on_any_overlap() -> None:
    # a Luhn-valid card number inside a valid IBAN: the IBAN came first
    assert find_personal_data("DE89 4111 1111 1111 1111 11") == [Finding("iban", 0, 27)]
    # the same digits with a wrong checksum: only the card is left
    assert find_personal_data("DE00 4111 1111 1111 1111") == [Finding("card", 5, 24)]
    # a mask that runs into a card number is dropped, not cut back
    assert find_personal_data("**** 4111 1111 1111 1111") == [Finding("card", 5, 24)]


# ---------------------------------------------------------------- clean_merchant

MERCHANTS = [
    ("ALDI SÜD\nMusterstr. 1\n44227 Dortmund", "ALDI SÜD"),
    ("Netto 44227 Dortmund", "Netto"),
    ("REWE Tel. 0231 123456", "REWE"),
    ("Beispiel Markt, www.beispiel.de", "Beispiel Markt"),
    ("dm-drogerie markt", "dm-drogerie markt"),
    ("ALDI", "ALDI"),
    ("  \n  Lidl  ", "Lidl"),
    (None, None),
    ("", None),
    ("Tel. 0231 123456", None),
    ("Shop ****1234", "Shop [card]"),
    # each cut, and the trailing punctuation
    ("Markt 0231/123456", "Markt"),
    ("Markt 0231-12 34 56", "Markt"),
    ("Markt https://markt.example", "Markt"),
    ("Markt | @markt", "Markt"),
    # the cut is at `@`, so an e-mail's local part stays (known limit)
    ("Markt | info@markt.example", "Markt | info"),
    ("Markt Telefon: 0231", "Markt"),
    ("Markt FON 0231", "Markt"),
    ("Markt fax", "Markt"),
    ("Markt – Fil. 3;", "Markt – Fil. 3"),
    ("Markt -", "Markt"),
    ("Markt:/,;", "Markt"),
    ("Markt\r\nHauptstraße 1", "Markt"),
    # no cut: short numbers, words that only contain a label, a store number
    ("Markt 24", "Markt 24"),
    ("Fontana Café", "Fontana Café"),
    ("Telekom Shop", "Telekom Shop"),
    ("REWE Filiale 1234", "REWE Filiale [id]"),
    ("   ", None),
    ("www.markt.example", None),
    # a first line with nothing left: the next non-blank line, up to three lines
    ("Tel. 0231 123456\nREWE", "REWE"),
    ("44227 Dortmund\n\nwww.markt.example\nNetto", "Netto"),
    ("Tel. 0231 123456\n44227\nwww.markt.example\nREWE", None),
    # the cut is at `@`, so a name after it is lost (accepted limit)
    ("Shop @ Home", "Shop"),
]


@pytest.mark.parametrize(("text", "expected"), MERCHANTS)
def test_clean_merchant(text: str | None, expected: str | None) -> None:
    assert clean_merchant(text) == expected


@pytest.mark.parametrize(("text", "expected"), MERCHANTS)
def test_clean_merchant_is_idempotent(text: str | None, expected: str | None) -> None:
    assert clean_merchant(expected) == expected


# ---------------------------------------------------------------- bounded input
# Every function looks at no more than MAX_TEXT_CHARS characters. A longer text is cut
# there (a little earlier so no escape, card number or IBAN is split) and gets TRUNCATED.

LIMIT = MAX_TEXT_CHARS
CARD = "4111 1111 1111 1111"


def test_a_text_at_the_limit_is_not_cut() -> None:
    text = "a" * (LIMIT - len(CARD) - 1) + " " + CARD
    assert len(text) == LIMIT
    assert redact_text(text) == text[: -len(CARD)] + "[card]"
    assert find_personal_data(text) == [Finding("card", LIMIT - len(CARD), LIMIT)]


def test_one_character_over_the_limit_is_cut() -> None:
    assert redact_text("a" * (LIMIT + 1)) == "a" * LIMIT + TRUNCATED


def test_nothing_after_the_cut_is_returned_or_searched() -> None:
    text = "a" * LIMIT + CARD + " Terminal-ID: 55501234"
    assert redact_text(text) == "a" * LIMIT + TRUNCATED
    assert find_personal_data(text) == []


NUMBERS = [CARD, "4111111111111111", "4111-1111-1111-1111", "DE89 3704 0044 0532 0130 00"]


@pytest.mark.parametrize(
    ("number", "before"),
    [(n, before) for n in NUMBERS for before in (1, 5, 14, 18, 26) if before < len(n)],
)
def test_a_number_across_the_cut_is_dropped_whole(number: str, before: int) -> None:
    # `before` characters of the number sit before the limit. Cut there, it would lose its
    # checksum and survive with most of its digits.
    head = "a" * (LIMIT - before - 1)
    result = redact_text(head + " " + number + " und mehr")
    assert result == head + TRUNCATED
    assert redact_text(result) == result


def test_a_labelled_id_across_the_cut_is_dropped() -> None:
    result = redact_text("a" * (LIMIT - 17) + " Terminal-ID 5550" + "1234")
    assert result.endswith(TRUNCATED) and "5550" not in result


ESCAPES = ["\\u00df", '\\"', "\\\\", "\\n"]


@pytest.mark.parametrize(
    ("escape", "before"), [(e, before) for e in ESCAPES for before in range(1, len(e))]
)
def test_the_cut_never_splits_a_json_escape(escape: str, before: int) -> None:
    # `before` characters of the escape sit before the limit, the rest after it.
    head = "a" * (LIMIT - before)
    result = redact_text(head + escape + "a" * 10)
    assert result == head + TRUNCATED
    assert redact_text(result) == result


def test_an_escape_that_ends_at_the_limit_is_kept() -> None:
    head = "a" * (LIMIT - 6) + "\\u00df"
    assert redact_text(head + "aaa") == head + TRUNCATED


def test_a_cut_json_string_still_decodes() -> None:
    raw = json.dumps({"merchant": "Straße " * (LIMIT // 7 + 10)}, ensure_ascii=True)
    body = redact_text(raw).removesuffix(TRUNCATED)
    assert len(body) <= LIMIT
    assert json.loads('"' + body.split('": "', 1)[1] + '"').startswith("Straße Straße")


def test_redaction_may_grow_a_text_past_the_limit_without_a_second_cut() -> None:
    # `[id]` is longer than `1`, so the result has more than LIMIT characters. Its
    # placeholders count as one character each, so a second pass doesn't cut it.
    text = "Bon.1," * (LIMIT // 6)
    once = redact_text(text)
    assert once == "Bon.[id]," * (LIMIT // 6)
    assert len(once) > LIMIT
    assert redact_text(once) == once


@pytest.mark.parametrize(
    "text",
    [
        "a" * (LIMIT + 1),
        "a" * LIMIT + TRUNCATED,
        "a" * (2 * LIMIT) + TRUNCATED,
        "Bon 1 " * LIMIT,
        "Bon.1," * (LIMIT // 6 + 100),
        "[card] " * LIMIT,
        "[id]" * (LIMIT + 1),
        "Karte ****4242 " * (LIMIT // 15 + 1) + CARD,
        "Markt [truncated]",
        "Bon 12 [truncated]",
    ],
)
def test_bounding_is_idempotent(text: str) -> None:
    once = redact_text(text)
    assert redact_text(once) == once


def test_a_short_text_that_ends_in_the_suffix_is_redacted_as_before() -> None:
    assert redact_text("Bon 12 [truncated]") == "Bon [id] [truncated]"
    assert redact_text("[truncated]") == "[truncated]"


def test_clean_merchant_is_bounded() -> None:
    name = "Markt " + "a" * LIMIT
    assert clean_merchant(name) == "Markt " + "a" * (LIMIT - 6) + TRUNCATED
    assert clean_merchant(clean_merchant(name)) == clean_merchant(name)
    # the name comes from a line before the cut: nothing of it was cut off
    assert clean_merchant("Markt\n" + "a" * LIMIT) == "Markt"
    # a line after the cut is never looked at
    assert clean_merchant("Tel. 0231 " * (LIMIT // 10 + 1) + "\nREWE") is None
    assert clean_merchant("Markt [truncated]") == "Markt [truncated]"


# ---------------------------------------------------------------- run time
# The guard against ReDoS: a regex so slow that one string freezes the pipeline. Two
# happened in F03: `card_masked` was exponential (5.7 s on 40 `*`) and `labelled_id`
# quadratic. Redaction only sees model output, capped by `LLM_MAX_TOKENS` (about 8-10 KB),
# and never looks at more than MAX_TEXT_CHARS (64k) characters. At that size a quadratic
# rule costs well under a second, so only exponential behaviour matters, and that blows
# up even on short inputs.
#
# So every hostile string below has exactly MAX_TEXT_CHARS characters, and each function
# runs on it once and must finish under SLOW_S. The margin: on a dev machine most cases
# take a few milliseconds at 64k, and the slowest (`redact_text` and `clean_merchant` on
# IBAN-shaped groups, where every word start is a checksum candidate) about 70 ms: about
# 30x below the limit, while the shared CI runner was about 2.3x slower than a dev
# machine. An exponential rule passes the limit after a few dozen characters
# (test_the_limit_catches_an_exponential_rule).
SLOW_S = 2.0


def seconds(func: Callable[[str], object], arg: str) -> float:
    """The duration of one call of `func(arg)`."""
    started = time.perf_counter()
    func(arg)
    return time.perf_counter() - started


def hostile(chunk: str, prefix: str = "", suffix: str = "") -> str:
    """`chunk` repeated between `prefix` and `suffix`: exactly MAX_TEXT_CHARS characters."""
    middle = LIMIT - len(prefix) - len(suffix)
    return prefix + (chunk * -(-middle // len(chunk)))[:middle] + suffix


ID_LABELS = [
    "Terminal-ID",
    "Trace-Nr",
    "Beleg-Nr",
    "Bon-Nr",
    "Kasse",
    "TA-Nr",
    "Transaktion",
    "TSE-Signatur",
    "Seriennr",
    "Kunden-Nr",
    "Kartennummer",
    "AID",
    "Genehmigungs-Nr",
    "Autorisierung",
    "VU-Nr",
    "Filiale",
    "Fil.-Nr",
    "TID",
]
LABELS = (
    "Terminal-ID Trace-Nr. Beleg Bon Kasse Transaktion TSE-Signatur Kunden-Nr. Filiale AID "
    "Tel. Fax USt-IdNr. Es bediente Sie Bediener Musterstraße 44227 "
)
SEPARATORS = "*" * 40 + "\n" + "-" * 40 + "\n" + "#" * 40 + "\n" + "X" * 40 + "\n"
HALF = LIMIT // 2
HOSTILE: dict[str, str] = {
    "*": hostile("*"),
    "X": hostile("X"),
    "x": hostile("x"),
    "#": hostile("#"),
    "digits": hostile("1"),
    "1234 groups": hostile("1234 "),
    "12345a groups": hostile("12345a"),
    "1 2 3 4 5 groups": hostile("1 2 3 4 5 a "),
    "uppercase": hostile("A"),
    "lowercase": hostile("a"),
    "-": hostile("-"),
    ".": hostile("."),
    "a.": hostile("a."),
    "a-": hostile("a-"),
    "spaces": hostile(" "),
    "punctuation": hostile(" ,;:-–|/"),
    "label repeated": hostile("Kasse "),
    "every label repeated": hostile(LABELS),
    "www. x.de": hostile("x.de ", prefix="www."),
    "**** groups": hostile("**** "),
    "label then spaces": "Tel" + " " * (HALF - 3) + ":" + " " * (LIMIT - HALF - 1),
    "a@": hostile("a@"),
    "word then @": hostile("a", suffix="@"),
    "capitalised word": hostile("a", prefix="A", suffix=" "),
    "0-12 groups": hostile("-12", prefix="0"),
    "postcodes": hostile("12345 "),
    "label, digit groups, a unit": hostile("1 ", prefix="Kasse ", suffix="kg"),
    "mask then digits": hostile("*", suffix="1234"),
    # IBAN-shaped runs: every word start is a candidate the checksum has to judge
    "DE89 groups": hostile("DE89 "),
    "AB12CDEF": hostile("AB12CDEF"),
    "AB12 CDEF groups": hostile("AB12 CDEF "),
    "GB82 WEST groups": hostile("GB82 WEST "),
    "AB12 C3D4 groups": hostile("AB12 C3D4 "),
    "DE89****": hostile("DE89****"),
    "DE89 0000 groups": hostile("DE89 0000 "),
    "uppercase and digit groups": hostile("C3D4 ", prefix="AB12 "),
    "masked IBAN groups": hostile("**** ", prefix="DE89 ", suffix="30"),
    "DE89XXXX": hostile("X", prefix="DE89", suffix="3000"),
    "valid IBAN repeated": hostile("DE89 3704 0044 0532 0130 00 "),
    # the label repeated without spaces, so one value run holds every label (quadratic
    # while a lookahead scanned ahead of the value run)
    **{
        f"{label!r} repeated{end}": hostile(label, suffix=end)
        for label in ("Bon-Nr.", "Kasse.", "AID.", "Filiale=", "TID/", "TID-")
        for end in ("", "1 kg")
    },
    **{
        f"labels joined by {joint!r}{end}": hostile(joint.join(ID_LABELS) + joint, suffix=end)
        for joint in "/.=-"
        for end in ("", "1 kg")
    },
    # separator lines and a full receipt, over and over
    "receipts": hostile(SEPARATORS + RECEIPT + "\n"),
    # one long merchant line: the store name, then a header that never hits a cut
    "merchant line": hostile("Filiale 1234 " + "*" * 20 + " 1234 " + " ,;:-|/ ", prefix="Markt "),
}


def test_the_hostile_strings_have_the_limit_size() -> None:
    assert {len(text) for text in HOSTILE.values()} == {LIMIT}


@pytest.mark.parametrize("text", HOSTILE.values(), ids=HOSTILE.keys())
def test_redact_text_is_fast_on_hostile_input(text: str) -> None:
    assert seconds(redact_text, text) < SLOW_S


@pytest.mark.parametrize("rule", RULES, ids=[f"{i}-{rule.kind}" for i, rule in enumerate(RULES)])
def test_every_rule_is_fast_on_hostile_input(rule) -> None:
    def scan(text: str) -> None:
        for _ in rule.pattern.finditer(text):
            pass

    durations = {name: seconds(scan, text) for name, text in HOSTILE.items()}
    assert {name: s for name, s in durations.items() if s >= SLOW_S} == {}


@pytest.mark.parametrize("text", HOSTILE.values(), ids=HOSTILE.keys())
def test_clean_merchant_is_fast_on_hostile_input(text: str) -> None:
    assert seconds(clean_merchant, text) < SLOW_S


def test_a_long_merchant_line_is_cleaned_and_redacted() -> None:
    line = HOSTILE["merchant line"]
    assert "\n" not in line
    cleaned = clean_merchant(line)
    assert cleaned and cleaned.startswith("Markt Filiale [id]") and "1234" not in cleaned


def test_a_long_realistic_text_is_redacted_and_idempotent() -> None:
    once = redact_text(HOSTILE["receipts"])
    assert "4242" not in once and "55501234" not in once
    assert "*" * 40 in once and "#" * 40 in once
    assert redact_text(once) == once


def test_runaway_text_is_idempotent() -> None:
    text = "Kasse 3 Bon 4 " * 1000 + "Karte ****4242 Terminal-ID: 55501234"
    once = redact_text(text)
    assert redact_text(once) == once


# The `card_masked` pattern before F03's fix: `(?:[ -]?[*Xx#]{2,})*` can split a run of
# `*` in exponentially many ways. It took 5.7 s on 40 `*` when it was found, and grows
# about 1.6x per character (0.8 s at 34 and about 14 s at 40 on a dev machine).
OLD_CARD_MASKED = r"[*Xx#]{4,}(?:[ -]?[*Xx#]{2,})*[ -]?\d{2,4}"
STARS = 44  # about 100 s on a dev machine, so even a runner 50x faster passes the limit


def test_the_limit_catches_an_exponential_rule() -> None:
    # In a child process that is killed at the limit, so it can't hang the suite. A child
    # that fails at once (a typo in the code) raises CalledProcessError and fails the test.
    code = f"import re; list(re.finditer({OLD_CARD_MASKED!r}, '*' * {STARS}))"
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run([sys.executable, "-c", code], timeout=SLOW_S, check=True)
    # the rule that replaced it makes nothing of the same string
    [rule] = [rule for rule in RULES if rule.kind == "card_masked"]
    assert seconds(lambda text: list(rule.pattern.finditer(text)), "*" * STARS) < 0.01

"""Redaction rules (decision 0017): each rule, the must-survive negatives, idempotence,
and `clean_merchant`."""

import json
import time

import pytest

from app.domain.redaction import (
    PLACEHOLDERS,
    RULES,
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


def test_iban_checksum_is_linear_on_a_long_input() -> None:
    # Digit by digit, no big integer: a million digits stay within a few budgets.
    assert timed(iban_checksum_valid, "DE00" + "9" * 1_000_000) < 5 * BUDGET_S


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


# ---------------------------------------------------------------- run time
# Receipts print separator lines like `*****` and `-----`. Every rule must stay linear on
# long runs of one character class: a backtracking rule once took 5.7 s on 40 `*`.

BUDGET_S = 0.5
RUN = 10_000
LONG = 28_000  # repetitions: about 200k characters, where a quadratic rule takes seconds
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
RUNS = {
    "*": "*" * RUN,
    "X": "X" * RUN,
    "x": "x" * RUN,
    "#": "#" * RUN,
    "digits": "1" * RUN,
    "1234 groups": "1234 " * (RUN // 5),
    "12345a groups": "12345a" * (RUN // 6),
    "1 2 3 4 5 groups": "1 2 3 4 5 a " * (RUN // 12),
    "uppercase": "A" * RUN,
    "lowercase": "a" * RUN,
    "-": "-" * RUN,
    ".": "." * RUN,
    "a.": "a." * (RUN // 2),
    "a-": "a-" * (RUN // 2),
    "spaces": " " * RUN,
    "punctuation": " ,;:-–|/" * (RUN // 8),
    "label repeated": "Kasse " * (RUN // 6),
    "every label repeated": LABELS * (RUN // len(LABELS)),
    "www. x.de": "www." + "x.de " * (RUN // 5),
    "**** groups": "**** " * (RUN // 5),
    "label then spaces": "Tel" + " " * RUN + ":" + " " * RUN,
    "a@": "a@" * (RUN // 2),
    "word then @": "a" * RUN + "@",
    "capitalised word": "A" + "a" * RUN + " ",
    "0-12 groups": "0" + "-12" * (RUN // 3),
    "postcodes": "12345 " * (RUN // 6),
    "label, digit groups, a unit": "Kasse " + "1 " * RUN + "kg",
    # IBAN-shaped runs: every word start is a candidate the checksum has to judge
    "DE89 groups": "DE89 " * (RUN // 5),
    "AB12CDEF": "AB12CDEF" * (RUN // 8),
    "AB12 CDEF groups": "AB12 CDEF " * (RUN // 10),
    "DE89 0000 groups": "DE89 0000 " * (RUN // 10),
    "uppercase and digit groups": "AB12 " + "C3D4 " * (RUN // 5),
    "masked IBAN groups": "DE89 " + "**** " * (RUN // 5) + "30",
    "DE89XXXX": "DE89" + "X" * RUN + "3000",
    "valid IBAN repeated": "DE89 3704 0044 0532 0130 00 " * (RUN // 28),
    # the same at about 200k characters
    **{
        f"{chunk!r} x {LONG * 7 // len(chunk)}": chunk * (LONG * 7 // len(chunk))
        for chunk in ("DE89 ", "AB12CDEF", "GB82 WEST ", "AB12 C3D4 ", "DE89****")
    },
    # Inputs that took 2.3 s at 28k repetitions when a lookahead scanned ahead of the
    # value run: the label repeated without spaces, so one value run holds every label.
    **{
        f"{label!r} x {LONG}{end}": label * LONG + end
        for label in ("Bon-Nr.", "Kasse.", "AID.", "Filiale=", "TID/", "TID-")
        for end in ("", "1 kg")
    },
    **{
        f"labels joined by {joint!r}{end}": (joint.join(ID_LABELS) + joint)
        * (LONG * 7 // len(joint.join(ID_LABELS)))
        + end
        for joint in "/.=-"
        for end in ("", "1 kg")
    },
}
SEPARATORS = "*" * 40 + "\n" + "-" * 40 + "\n" + "#" * 40 + "\n" + "X" * 40 + "\n"
MIXED = (SEPARATORS + RECEIPT + "\n") * (20_000 // len(SEPARATORS + RECEIPT + "\n") + 1)
# One long merchant line: the store name, then a header that never hits a cut.
MERCHANT_LINE = ("Markt " + "Filiale 1234 " + "*" * 20 + " 1234 " + " ,;:-|/ ") * (20_000 // 52)


def timed(func, *args) -> float:
    started = time.perf_counter()
    func(*args)
    return time.perf_counter() - started


@pytest.mark.parametrize("text", RUNS.values(), ids=RUNS.keys())
def test_redact_text_is_fast_on_long_runs(text: str) -> None:
    assert timed(redact_text, text) < BUDGET_S


@pytest.mark.parametrize("rule", RULES, ids=[f"{i}-{rule.kind}" for i, rule in enumerate(RULES)])
def test_every_rule_is_fast_on_long_runs(rule) -> None:
    slow = {
        name: seconds
        for name, text in RUNS.items()
        if (seconds := timed(lambda t: list(rule.pattern.finditer(t)), text)) >= BUDGET_S
    }
    assert slow == {}


@pytest.mark.parametrize("text", RUNS.values(), ids=RUNS.keys())
def test_clean_merchant_is_fast_on_long_runs(text: str) -> None:
    assert timed(clean_merchant, text) < BUDGET_S


def test_clean_merchant_is_fast_on_a_20_kb_line() -> None:
    assert len(MERCHANT_LINE) >= 20_000 and "\n" not in MERCHANT_LINE
    assert timed(clean_merchant, MERCHANT_LINE) < BUDGET_S
    cleaned = clean_merchant(MERCHANT_LINE)
    assert cleaned and cleaned.startswith("Markt Filiale [id]") and "1234" not in cleaned
    assert timed(clean_merchant, MIXED) < BUDGET_S


def test_realistic_mixed_text_is_fast_and_redacted() -> None:
    assert len(MIXED) >= 20_000
    assert timed(redact_text, MIXED) < BUDGET_S
    once = redact_text(MIXED)
    assert "4242" not in once and "55501234" not in once
    assert "*" * 40 in once and "#" * 40 in once
    assert redact_text(once) == once


def test_runaway_text_is_idempotent() -> None:
    text = "Kasse 3 Bon 4 " * 1000 + "Karte ****4242 Terminal-ID: 55501234"
    once = redact_text(text)
    assert redact_text(once) == once

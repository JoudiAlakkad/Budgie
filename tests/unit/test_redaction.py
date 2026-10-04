"""Redaction rules (decision 0017): each rule, the must-survive negatives, idempotence."""

import json
import time

import pytest

from app.domain.redaction import (
    ALL_KINDS,
    DESCRIPTION_KINDS,
    PLACEHOLDERS,
    RULES,
    Finding,
    find_personal_data,
    luhn_valid,
    redact_description,
    redact_text,
)

# (rule kind, input, expected output)
POSITIVES = [
    ("iban", "DE89 3704 0044 0532 0130 00", "[iban]"),
    ("iban", "IBAN: DE89370400440532013000", "IBAN: [iban]"),
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
    ("email", "info@beispiel-markt.de", "[email]"),
    ("email", "Kontakt: max.muster+bon@example.com", "Kontakt: [email]"),
    ("url", "https://www.beispiel-markt.de/filialen", "[url]"),
    ("url", "www.example-shop.de", "[url]"),
    ("url", "Besuchen Sie example.de!", "Besuchen Sie [url]!"),
    ("url", "service.com/hilfe", "[url]"),
    ("phone", "+49 231 123456", "[phone]"),
    ("phone", "0231/123456", "[phone]"),
    ("phone", "0231-12 34 56", "[phone]"),
    ("phone", "Tel. 0231 123456", "Tel. [phone]"),
    ("phone", "Fon: 0231 9876543", "Fon: [phone]"),
    ("phone", "Fax 0231 123457", "Fax [phone]"),
    ("phone", "Telefon: 0231123456", "Telefon: [phone]"),
    ("taxid", "DE123456789", "[taxid]"),
    ("taxid", "USt-IdNr.: DE123456789", "USt-IdNr.: [taxid]"),
    ("taxid", "USt-ID DE987654321", "USt-ID [taxid]"),
    ("taxid", "St.-Nr. 315/5802/1234", "St.-Nr. [taxid]"),
    ("taxid", "Steuernummer: 315/5802/1234", "Steuernummer: [taxid]"),
    ("street", "Hauptstraße 12", "[address]"),
    ("street", "Beispielstr. 12a", "[address]"),
    ("street", "Am Lindenplatz 3", "[address]"),
    ("street", "An der Ruhrallee 5", "[address]"),
    ("street", "Kölner Straße 12-14", "[address]"),
    ("street", "MUSTERWEG 7", "[address]"),
    ("street", "Musterstraße 12a, 44227 Dortmund", "[address], [address]"),
    ("street", "Hauptstraße 12 44227 Dortmund", "[address] [address]"),
    ("street", "Hauptstraße 12\n44227 Dortmund", "[address]\n[address]"),
    ("street", '{"merchant": "Markt, Lindenweg 4"}', '{"merchant": "Markt, [address]"}'),
    ("postcode_city", "44227 Dortmund", "[address]"),
    ("postcode_city", "80331 München", "[address]"),
    ("postcode_city", "01067 DRESDEN", "[address]"),
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
    ("cashier", "Es bediente Sie: Anna", "Es bediente Sie: [name]"),
    ("cashier", "Es bediente Sie Anna M.", "Es bediente Sie [name]"),
    ("cashier", "Kassierer(in): Max", "Kassierer(in): [name]"),
    ("cashier", "Bediener: Lisa Muster", "Bediener: [name]"),
    ("cashier", "ES BEDIENTE SIE: ANNA", "ES BEDIENTE SIE: [name]"),
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
]

# Item names that end in a street suffix and a count. The full table can't tell the
# count from a house number when nothing follows; line items use the description mode.
ADDRESS_LIKE_ITEMS = [
    "Hering 2",
    "APFELRING 2",
    "STREUSELRING 1",
    "Holzweg 2",
    "KÖNIGSBERGER PLATZ 2",
    "10000 BONUSPUNKTE",
    "12345 Meilen",
]
FULL_MODE_LIMITS = {"APFELRING 2", "STREUSELRING 1", "Holzweg 2", "KÖNIGSBERGER PLATZ 2"}


@pytest.mark.parametrize(("kind", "text", "expected"), POSITIVES)
def test_rule_redacts(kind: str, text: str, expected: str) -> None:
    assert redact_text(text) == expected
    assert kind in {f.kind for f in find_personal_data(text)}


@pytest.mark.parametrize("text", NEGATIVES)
def test_must_survive(text: str) -> None:
    assert find_personal_data(text) == []
    assert redact_text(text) == text


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            text,
            marks=pytest.mark.xfail(
                strict=True,
                reason="full mode can't tell a trailing item count from a house number",
            ),
        )
        if text in FULL_MODE_LIMITS
        else text
        for text in ADDRESS_LIKE_ITEMS
    ],
)
def test_address_like_items_survive_the_full_table(text: str) -> None:
    assert redact_text(text) == text


@pytest.mark.parametrize("text", ADDRESS_LIKE_ITEMS + NEGATIVES)
def test_address_like_items_survive_the_description_mode(text: str) -> None:
    assert find_personal_data(text, kinds=DESCRIPTION_KINDS) == []
    assert redact_description(text) == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Karte ****4242", "Karte [card]"),
        ("Terminal-ID: 55501234", "Terminal-ID: [id]"),
        ("Tel. 0231 9876543", "Tel. [phone]"),
        ("Es bediente Sie: Erika", "Es bediente Sie: [name]"),
        ("www.beispiel-markt.de", "[url]"),
        ("Musterstraße 12a, 44227 Dortmund", "Musterstraße 12a, 44227 Dortmund"),
    ],
)
def test_description_mode_keeps_every_rule_but_addresses(text: str, expected: str) -> None:
    assert redact_description(text) == expected


def test_description_kinds_are_all_but_the_address_rules() -> None:
    assert {rule.kind for rule in RULES} == ALL_KINDS
    left_out = ALL_KINDS - DESCRIPTION_KINDS
    assert left_out == {"street", "postcode_city"}


def test_kinds_none_is_the_full_table() -> None:
    assert redact_text(RECEIPT, kinds=None) == redact_text(RECEIPT)
    assert redact_text(RECEIPT, kinds=ALL_KINDS) == redact_text(RECEIPT)


def test_kinds_select_rules() -> None:
    assert redact_text("Tel. 0231 9876543 ****4242", kinds={"phone"}) == "Tel. [phone] ****4242"


def test_unknown_kind_is_an_error() -> None:
    with pytest.raises(ValueError, match="addresss"):
        redact_text("x", kinds={"addresss"})


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
    assert find_personal_data(f"Kasse {placeholder} Tel. {placeholder}") == []


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

RECEIPT_REDACTED = """Beispiel Markt GmbH
[address]
[address]
Tel. [phone]
[url]
USt-IdNr.: [taxid]

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
Es bediente Sie: [name]
Kasse: [id] Bon [id]
Vielen Dank für Ihren Einkauf!"""


def test_synthetic_receipt() -> None:
    assert redact_text(RECEIPT) == RECEIPT_REDACTED
    assert redact_text(RECEIPT_REDACTED) == RECEIPT_REDACTED


def test_json_stays_valid() -> None:
    data = {
        "is_receipt": True,
        "merchant": "Beispiel Markt\nMusterstraße 12a\n44227 Dortmund",
        "date": "2026-09-17",
        "line_items": [
            {"description": "Karte ****4242", "qty": None, "unit_price": None, "amount": 7.12},
            {"description": 'Gutschein "Tel. 0231 123456"', "qty": 1, "unit_price": 0.5},
        ],
        "total": 7.12,
        "unreadable_fields": ["www.beispiel-markt.de", "Es bediente Sie: Erika"],
        "raw": RECEIPT,
    }
    for document in (json.dumps(data, indent=2), json.dumps(data, ensure_ascii=False)):
        redacted = json.loads(redact_text(document))
        assert redacted["merchant"] == "Beispiel Markt\n[address]\n[address]"
        assert redacted["line_items"][0]["description"] == "Karte [card]"
        assert redacted["line_items"][1]["description"] == 'Gutschein "Tel. [phone]"'
        assert redacted["unreadable_fields"] == ["[url]", "Es bediente Sie: [name]"]
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


def test_luhn_invalid_card_number_survives() -> None:
    assert redact_text("4111 1111 1111 1112") == "4111 1111 1111 1112"


def test_findings_sorted_and_not_overlapping() -> None:
    findings = find_personal_data(RECEIPT)
    assert findings == sorted(findings, key=lambda f: f.start)
    assert all(a.end <= b.start for a, b in zip(findings, findings[1:], strict=False))
    assert [f.kind for f in findings] == [
        "street",
        "postcode_city",
        "phone",
        "url",
        "taxid",
        "card_masked",
        "labelled_id",
        "labelled_id",
        "cashier",
        "labelled_id",
        "labelled_id",
    ]


def test_earlier_rule_wins_on_overlap() -> None:
    # The url rule also matches the domain, but the e-mail rule comes first.
    assert find_personal_data("an info@example.de") == [Finding("email", 3, 18)]


# ---------------------------------------------------------------- run time
# Receipts print separator lines like `*****` and `-----`. Every rule must stay linear on
# long runs of one character class: a backtracking rule once took 5.7 s on 40 `*`.

BUDGET_S = 0.5
RUN = 10_000
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
    "uppercase": "A" * RUN,
    "lowercase": "a" * RUN,
    "-": "-" * RUN,
    ".": "." * RUN,
    "a.": "a." * (RUN // 2),
    "a-": "a-" * (RUN // 2),
    "spaces": " " * RUN,
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
}
SEPARATORS = "*" * 40 + "\n" + "-" * 40 + "\n" + "#" * 40 + "\n" + "X" * 40 + "\n"
MIXED = (SEPARATORS + RECEIPT + "\n") * (20_000 // len(SEPARATORS + RECEIPT + "\n") + 1)


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


def test_realistic_mixed_text_is_fast_and_redacted() -> None:
    assert len(MIXED) >= 20_000
    assert timed(redact_text, MIXED) < BUDGET_S
    once = redact_text(MIXED)
    assert "Musterstraße" not in once and "4242" not in once
    assert "*" * 40 in once and "#" * 40 in once
    assert redact_text(once) == once


def test_runaway_url_text_is_idempotent() -> None:
    text = "www." + "x.de " * 1000 + "Tel 0231 1234567 Kasse 3 Bon 4"
    once = redact_text(text)
    assert redact_text(once) == once

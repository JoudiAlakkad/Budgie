"""Deterministic removal of personal data from model output (decision 0017).

A fixed, ordered rule table finds IBANs, card numbers, contact data, tax ids, addresses,
labelled ids and cashier names. Earlier rules win on overlap. Rules with a `value` group
keep their label and replace only the value. Placeholders never match a rule, so
redaction is idempotent. Rules see JSON escapes decoded, and no match crosses a `"` or
splits an escape, so redacting a JSON document keeps it valid.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

Check = Callable[[str], bool]


@dataclass(frozen=True)
class Finding:
    """A span of `text[start:end]` that holds personal data of the given kind."""

    kind: str
    start: int
    end: int


@dataclass(frozen=True)
class Rule:
    """One row of the rule table; `check` filters matches the regex can't judge."""

    kind: str
    pattern: re.Pattern[str]
    placeholder: str
    check: Check | None = None


def luhn_valid(digits: str) -> bool:
    """True if the digit string passes the Luhn checksum used by payment cards."""
    total = 0
    for i, char in enumerate(reversed(digits)):
        d = int(char) * (2 if i % 2 else 1)
        total += d - 9 if d > 9 else d
    return total % 10 == 0


def _ean13_valid(digits: str) -> bool:
    """True if the 13 digits carry a valid EAN-13 check digit."""
    weighted = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(digits[:12]))
    return len(digits) == 13 and (10 - weighted % 10) % 10 == int(digits[12])


def _is_card(match: str) -> bool:
    digits = re.sub(r"\D", "", match)
    return 13 <= len(digits) <= 19 and luhn_valid(digits) and not _ean13_valid(digits)


def _is_phone(match: str) -> bool:
    return 7 <= len(re.sub(r"\D", "", match)) <= 15


# Labels whose value is an id; also never a city name after a postcode.
_ID_LABEL = (
    r"(?i:Terminal(?:-?ID)?|TID|Trace(?:-?Nr\.?)?|TA-?Nr\.?|Beleg(?:-?Nr\.?)?|Bon(?:-?Nr\.?)?"
    r"|Kasse|Transaktion(?:s-?Nr\.?)?|TSE(?:[- ]?(?:Seriennummer|Signatur|Transaktion))?"
    r"|Seriennr\.?|Signatur|Kunden-?Nr\.?|Kundennummer|Karten-?Nr\.?|Kartennummer|AID"
    r"|Genehmigung(?:s-?Nr\.?)?|Autorisierung|VU-?Nr\.?|Filiale|Fil\.?-?Nr\.?)"
)
_NOT_CITY = r"(?!(?i:EUR|Euro|STK|Stück|KG|Uhr|Datum|Zeit|Punkte)\b|" + _ID_LABEL + r"\b)"
_PHONE = (
    r"(?:\+\d{1,3}[ ]?(?:\(0\)[ ]?)?|\(?0)\d{1,%(lead)s}\)?(?:[ /-]{1,2}\d{2,}){%(groups)s}"
    r"(?![\d.,:])"
)
_PHONE_LABEL = r"(?i:\b(?:Tel(?:efon)?|Fon|Fax|Mobil|Phone)\b\.?[ ]*:?[ ]*)"
# A label allows an unseparated number; without one, a separator or `+` is required.
_PHONE_LABELLED = _PHONE_LABEL + "(?P<value>" + _PHONE % {"lead": 14, "groups": "0,4"} + ")"
_PHONE_BARE = r"(?<![\w.,/:+-])" + _PHONE % {"lead": 5, "groups": "1,4"}
_TAXID_LABEL = (
    r"(?i:\b(?:USt-?Id(?:-?Nr)?|USt-?ID|UID|St\.?-?Nr|Steuer-?(?:nummer|nr))\b\.?[ ]*:?[ ]*)"
)
_URL_TAIL = r"[^\s\"'<>\\]*[^\s\"'<>\\.,;:)]"
_TLD = r"(?i:de|com|net|org|eu|info|shop|io|at|ch|biz)"
_DOMAIN = (
    r"(?<![\w.@-])[A-Za-z0-9][\w-]*(?:\.[\w-]+)*\." + _TLD + r"\b(?!\.\w)(?:/" + _URL_TAIL + ")?"
)
_STREET = (
    r"(?<![\w-])(?:(?i:am|an der|an den|auf dem|auf der|im|in der|zum|zur)[ ]+)?"
    r"(?:[A-ZÄÖÜ][\w-]*?|[A-ZÄÖÜ][\w-]*[ ](?=[A-ZÄÖÜ]))"
    r"(?i:stra(?:ße|sse)|str\.|weg|platz|allee|gasse|ring|damm|ufer)[ ]*"
    # house number (12, 12a, 12-14), not a price, weight or count
    r"\d{1,4}[a-zA-Z]?(?:[ ]?[-/][ ]?\d{1,4}[a-zA-Z]?)?(?!\w|[,.:]\d)"
    r"(?![ ]?(?i:stk|kg|g|x|l|ml)\b|[ ]?%)"
)
_POSTCODE_CITY = (
    r"(?<![\w.,/])\d{5}[ ]+" + _NOT_CITY + r"(?:[A-ZÄÖÜ][a-zäöüß]+|[A-ZÄÖÜ]{3,})\b"
    r"(?:-[A-ZÄÖÜ][\wäöüß]+|[ ](?:am|an der|a\.)[ ][A-ZÄÖÜ][\wäöüß]+)?"
)
# The value is not a date, price or time, holds a digit, and may go on with digit tokens.
_LABELLED_ID = (
    r"\b" + _ID_LABEL + r"(?![\w-])\.?[ ]*(?:[:#]|(?i:Nr)\.?:?)?[ ]*"
    r"(?P<value>(?!\d{1,2}\.\d{1,2}\.\d{2,4}\b|\d+,\d{2}\b|\d{1,2}:\d{2})"
    r"(?=[\w/.+=-]*\d)[\w/.+=-]*[\w=](?:[ ]\d[\d/-]*(?=\s|\"|\\|$))*)"
)
_CASHIER = (
    r"(?i:\b(?:Es[ ]bediente[ ]Sie|Kassierer(?:\(in\)|in)?|Bediener(?:in)?|Bedienung))(?!\w)"
    r"[ ]*:?[ ]*(?P<value>(?!" + _ID_LABEL + r"\b)[A-ZÄÖÜ][\wäöüß-]*"
    r"(?:[ ](?!" + _ID_LABEL + r"\b)[A-ZÄÖÜ](?:\.|[a-zäöüß]+\b))?)"
)


def _rule(kind: str, pattern: str, placeholder: str, check: Check | None = None) -> Rule:
    return Rule(kind, re.compile(pattern), placeholder, check)


RULES: tuple[Rule, ...] = (
    _rule("iban", r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,3})?\b", "[iban]"),
    _rule("card", r"(?<![\w.,])\d{4,6}(?:[ -]?\d{4,6}){1,3}(?![\w]|[.,]\d)", "[card]", _is_card),
    _rule("card_masked", r"(?<![\w*#])[*Xx#]{4,}(?:[ -]?[*Xx#]{2,})*[ -]?\d{2,4}(?!\d)", "[card]"),
    _rule("email", r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b", "[email]"),
    _rule("url", r"(?i:https?://|www\.)" + _URL_TAIL, "[url]"),
    _rule("url", _DOMAIN, "[url]"),
    _rule("phone", _PHONE_LABELLED, "[phone]", _is_phone),
    _rule("phone", _PHONE_BARE, "[phone]", _is_phone),
    _rule("taxid", _TAXID_LABEL + r"(?P<value>(?:[A-Z]{2,3}[ ]?)?\d+(?:/\d+)*)", "[taxid]"),
    _rule("taxid", r"\b(?:DE\d{9}|ATU\d{8})\b", "[taxid]"),
    _rule("street", _STREET, "[address]"),
    _rule("postcode_city", _POSTCODE_CITY, "[address]"),
    _rule("labelled_id", _LABELLED_ID, "[id]"),
    _rule("cashier", _CASHIER, "[name]"),
)

PLACEHOLDERS: frozenset[str] = frozenset(rule.placeholder for rule in RULES)
_PLACEHOLDER = {rule.kind: rule.placeholder for rule in RULES}

_ESCAPE = re.compile(r"\\(?:u[0-9a-fA-F]{4}|[\"\\/bfnrt])")
_DECODED = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


def _unescape(text: str) -> tuple[str, list[int]]:
    """Decode JSON escapes so rules see `ß`, not `\\u00df`.

    Returns the decoded text and, per decoded character (plus one past the end), its
    offset in `text`. An escape decodes to one character, so a match never splits it.
    """
    chars: list[str] = []
    offsets: list[int] = []
    last = 0
    for match in _ESCAPE.finditer(text):
        chars += text[last : match.start()]
        offsets += range(last, match.start())
        code = match.group()[1:]
        chars.append(chr(int(code[1:], 16)) if code[0] == "u" else _DECODED.get(code, code))
        offsets.append(match.start())
        last = match.end()
    chars += text[last:]
    offsets += range(last, len(text) + 1)
    return "".join(chars), offsets


def find_personal_data(text: str) -> list[Finding]:
    """Return the personal-data spans in `text`, sorted by start and never overlapping."""
    scan, offsets = _unescape(text)
    taken: list[Finding] = []
    for rule in RULES:
        for match in rule.pattern.finditer(scan):
            group = "value" if "value" in rule.pattern.groupindex else 0
            start, end = match.span(group)
            if rule.check and not rule.check(match.group(group)):
                continue
            if all(end <= f.start or f.end <= start for f in taken):
                taken.append(Finding(rule.kind, start, end))
    found = (Finding(f.kind, offsets[f.start], offsets[f.end]) for f in taken)
    return sorted(found, key=lambda f: f.start)


def redact_text(text: str) -> str:
    """Replace every finding with its typed placeholder. Idempotent."""
    parts: list[str] = []
    last = 0
    for finding in find_personal_data(text):
        parts += [text[last : finding.start], _PLACEHOLDER[finding.kind]]
        last = finding.end
    return "".join(parts) + text[last:]

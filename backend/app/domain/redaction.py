"""Deterministic removal of personal data from model output (decision 0017).

The response schema (`ai/schema.py`) is the first line of defence: it fixes every key,
type and enum, so free text reaches only three places: the line-item descriptions (where
payment and terminal lines slip in), `merchant` (where the receipt header slips in) and
the stored raw output. This module is the safety net for those three.

- `redact_text(text)` runs a small, ordered rule table: IBAN, card number, masked card
  digits and labelled ids (terminal, trace, receipt, till, TSE, ...). Earlier rules win
  on overlap. A rule with a `value` group keeps its label and replaces only the value.
  Placeholders never match a rule, so redaction is idempotent. Rules see JSON escapes
  decoded, and no match crosses a `"` or splits an escape, so redacting the raw JSON
  output keeps it valid.
- `clean_merchant(text)` keeps the first line of `merchant` up to the first sign of the
  receipt header's contact block (postcode, phone, URL, e-mail, phone label), then
  redacts what is left.

Every regex runs in linear time: no repeat can split its input in more than one way.
"""

import re
from bisect import bisect_left
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


# Labels whose value is an id.
_ID_LABEL = (
    r"(?i:Terminal(?:-?ID)?|TID|Trace(?:-?Nr\.?)?|TA-?Nr\.?|Beleg(?:-?Nr\.?)?|Bon(?:-?Nr\.?)?"
    r"|Kasse|Transaktion(?:s-?Nr\.?)?|TSE(?:[- ]?(?:Seriennummer|Signatur|Transaktion))?"
    r"|Seriennr\.?|Signatur|Kunden-?Nr\.?|Kundennummer|Karten-?Nr\.?|Kartennummer|AID"
    r"|Genehmigung(?:s-?Nr\.?)?|Autorisierung|VU-?Nr\.?|Filiale|Fil\.?-?Nr\.?)"
)
# Every repeat below has one way to split its input: possessive runs (`*+`, `++`), an
# atomic group or a mandatory separator, so no rule backtracks exponentially or
# quadratically.
#
# The value is not a date, price (either decimal separator), time or quantity with a
# unit; it holds a digit and may go on with digit tokens. The token is atomic, so the
# checks after it can't be dodged by giving characters back.
_LABELLED_ID = (
    r"\b" + _ID_LABEL + r"(?![\w-])\.?[ ]*+(?:[:#]|(?i:Nr)\.?:?)?[ ]*+"
    r"(?P<value>(?!\d{1,2}\.\d{1,2}\.\d{2,4}\b|\d{4}-\d{2}-\d{2}\b"
    r"|\d+[.,]\d{1,2}\b(?![.,]\d)|\d{1,2}:\d{2})"
    r"(?=[\w/.+=-]*\d)(?>[\w/.+=-]*[\w=])"
    r"(?!,\d|[ ]?(?:(?i:kg|g|l|ml|stk|stück|x)\b|%))"
    r"(?:[ ]\d[\d/-]*+(?=\s|\"|\\|$))*+)"
)


def _rule(kind: str, pattern: str, placeholder: str, check: Check | None = None) -> Rule:
    return Rule(kind, re.compile(pattern), placeholder, check)


RULES: tuple[Rule, ...] = (
    _rule("iban", r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,3})?\b", "[iban]"),
    _rule("card", r"(?<![\w.,])\d{4,6}(?:[ -]?\d{4,6}){1,3}(?![\w]|[.,]\d)", "[card]", _is_card),
    _rule(
        "card_masked",
        # A mask run starts only at the start of the run, never after a `**** ` group
        # (two mask characters, so `MAX ****1234` still matches).
        r"(?<![\w*#])(?<![*Xx#]{2}[ -])[*Xx#]{4,}+(?:[ -][*Xx#]{2,}+)*+[ -]?\d{2,4}(?!\d)",
        "[card]",
    ),
    _rule("labelled_id", _LABELLED_ID, "[id]"),
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
    # The spans taken so far, sorted by start; they never overlap, so the ends are sorted too.
    starts: list[int] = []
    taken: list[Finding] = []
    for rule in RULES:
        group = "value" if "value" in rule.pattern.groupindex else 0
        for match in rule.pattern.finditer(scan):
            start, end = match.span(group)
            if rule.check and not rule.check(match.group(group)):
                continue
            i = bisect_left(starts, start)
            if (i > 0 and taken[i - 1].end > start) or (i < len(taken) and taken[i].start < end):
                continue
            starts.insert(i, start)
            taken.insert(i, Finding(rule.kind, start, end))
    return [Finding(f.kind, offsets[f.start], offsets[f.end]) for f in taken]


def redact_text(text: str) -> str:
    """Replace every finding with its typed placeholder. Idempotent."""
    parts: list[str] = []
    last = 0
    for finding in find_personal_data(text):
        parts += [text[last : finding.start], _PLACEHOLDER[finding.kind]]
        last = finding.end
    return "".join(parts) + text[last:]


# Where the receipt header's contact block starts: a five-digit postcode, a phone-like
# run of six or more digits (single spaces, `/` or `-` between them), a URL, an e-mail
# or a phone label. Each alternative is bounded or has one way to split its input.
_MERCHANT_CUT = re.compile(
    r"\b\d{5}\b"
    r"|\d(?:[ /-]?\d){5,}+"
    r"|(?i:http|www\.)"
    r"|@"
    r"|(?i:\b(?:Tel|Telefon|Fon|Fax)\b)"
)
_MERCHANT_TRAILING = ",;:-–|/"


def clean_merchant(text: str | None) -> str | None:
    """Keep the store name from the model's `merchant`, without the header around it.

    Takes the first non-blank line, cuts it before the first postcode, phone number,
    URL, e-mail or phone label, strips whitespace and trailing punctuation, and redacts
    the rest. Returns `None` if nothing is left.
    """
    if text is None:
        return None
    line = next((line for line in text.splitlines() if line.strip()), "")
    if cut := _MERCHANT_CUT.search(line):
        line = line[: cut.start()]
    end = len(line)
    while end and (line[end - 1].isspace() or line[end - 1] in _MERCHANT_TRAILING):
        end -= 1
    name = line[:end].lstrip()
    return redact_text(name) if name else None

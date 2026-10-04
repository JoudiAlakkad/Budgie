"""Deterministic removal of personal data from model output (decision 0017).

The response schema (`ai/schema.py`) is the first line of defence: it fixes every key,
type and enum, so free text reaches only three places: the line-item descriptions (where
payment and terminal lines slip in), `merchant` (where the receipt header slips in) and
the stored raw output. This module is the safety net for those three.

- `redact_text(text)` runs a small, ordered rule table: IBAN, card number, masked card
  digits and labelled ids (terminal, trace, receipt, till, TSE, ...). Earlier rules win
  on overlap; a `labelled_id` value that runs into an earlier finding is cut back to the
  part before it (`Bon 12 <card>` -> `Bon [id] [card]`). A rule with a `value` group
  keeps its label and replaces only the value. Placeholders never match a rule, so
  redaction is idempotent. Rules see JSON escapes decoded, and no match crosses a `"` or
  splits an escape, so redacting the raw JSON output keeps it valid.
- `clean_merchant(text)` keeps the first line of `merchant` that still holds a name once
  the receipt header's contact block (postcode, phone, URL, e-mail, phone label) is cut
  off, then redacts what is left.

Linear time: no regex here has a repeat that can split its input in more than one way,
and `labelled_id` has no lookahead: it matches label, separator and value run, and
`_fit_id` judges the value in Python. `finditer` goes on after every match, accepted or
not. tests/unit/test_redaction.py times every rule on long runs of each character class
and of every label joined by `/`, `.`, `=` and `-`.
"""

import re
from bisect import bisect_left
from collections.abc import Callable
from dataclasses import dataclass

Check = Callable[[str], bool]
# (text, value start, value end) -> the end of the accepted value, or None to drop it.
Fit = Callable[[str, int, int], int | None]


@dataclass(frozen=True)
class Finding:
    """A span of `text[start:end]` that holds personal data of the given kind."""

    kind: str
    start: int
    end: int


@dataclass(frozen=True)
class Rule:
    """One row of the rule table.

    `check` filters matches the regex can't judge. `fit` does the same for rules whose
    value may be shortened: it gets the span (possibly cut back to the start of an
    earlier finding) and returns the end of the value to redact, or None.
    """

    kind: str
    pattern: re.Pattern[str]
    placeholder: str
    check: Check | None = None
    fit: Fit | None = None


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


# Labels whose value is an id. A trailing `.` is matched after the label, not in it, so
# every label ends in a letter and `\b` can close it.
_ID_LABEL = (
    r"(?i:Terminal(?:-?ID)?|TID|Trace(?:-?Nr)?|TA-?Nr|Beleg(?:-?Nr)?|Bon(?:-?Nr)?"
    r"|Kasse|Transaktion(?:s-?Nr)?|TSE(?:[- ]?(?:Seriennummer|Signatur|Transaktion))?"
    r"|Seriennr|Signatur|Kunden-?Nr|Kundennummer|Karten-?Nr|Kartennummer|AID"
    r"|Genehmigung(?:s-?Nr)?|Autorisierung|VU-?Nr|Filiale|Fil\.?-?Nr)"
)
_GAP = "[ \t\u00a0]*+"
# Label, separator and an atomic value run, and no lookahead. The separator is a hyphen
# (`TID-123`), or spaces, tabs or no-break spaces around an optional `:`, `#` or `Nr.`.
# The value is one token of word characters and `/.+=-`, ending in a word character or
# `=` (base64), then any space-separated digit groups (`5317 4492`). Whether it is an id
# is decided by `_fit_id`.
_LABELLED_ID = (
    r"\b" + _ID_LABEL + r"\b\.?(?:-|" + _GAP + r"(?:[:#]|(?i:Nr)\.?:?)?" + _GAP + r")"
    r"(?P<value>(?>[\w/.+=-]*[\w=])(?:[ ]\d[\d/-]*+)*+)"
)
# Not an id when the value's first token starts with one of these: a date (`17.09.2026`,
# `01/09/26`, `2026-09-17`), a price (`2.49`, `12,5`), a thousands number (`3.500`) or a
# quantity with its unit (`500g`, `2kg`, `3x`). Matched once per match, anchored.
_NOT_AN_ID = re.compile(
    r"(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2}|\d+[.,]\d{1,2}|\d{1,3}(?:[.,]\d{3})+"
    r"|\d+(?:[.,]\d+)?(?i:kg|g|l|ml|stk|stück|x))(?!\w)"
)
# What may not follow a value: the rest of a decimal (`2,5`) or a time (`14:32`), a unit
# (`5 kg`, `3 x`) or a percent sign. Matched once per step, anchored.
_NOT_AFTER_AN_ID = re.compile(r"[,:]\d|[ ]?(?:(?i:kg|g|l|ml|stk|stück|x)\b|%)")
_GROUP_END = frozenset(' \t\n\r\f\v\u00a0"\\')
_TRAILING = " \t\u00a0/.+-"


def _fit_id(text: str, start: int, end: int) -> int | None:
    """The end of the id in `text[start:end]`, or None if the value is no id.

    `end` may have been cut back to an earlier finding, so trailing separators go first.
    The last digit group is dropped while it doesn't end at whitespace, a quote, a
    backslash or the end of the text, or is followed by a unit. The first token must hold
    a digit and must not be a date, price, time or quantity; after a hyphen (`TID-123`)
    it must start with a digit, so `Beleg-Kopie 2` is no id. Linear in `end - start`.
    """
    while end > start and text[end - 1] in _TRAILING:
        end -= 1
    while end > start:
        space = text.rfind(" ", start, end)
        clean_end = _NOT_AFTER_AN_ID.match(text, end) is None
        if space < 0:
            if not clean_end:
                return None
            break
        if clean_end and (end == len(text) or text[end] in _GROUP_END):
            break
        end = space
    token = text[start:end].split(" ", 1)[0]
    if not any(char.isdigit() for char in token) or _NOT_AN_ID.match(token):
        return None
    if start and text[start - 1] == "-" and not token[0].isdigit():
        return None
    return end


def _rule(
    kind: str,
    pattern: str,
    placeholder: str,
    check: Check | None = None,
    fit: Fit | None = None,
) -> Rule:
    return Rule(kind, re.compile(pattern), placeholder, check, fit)


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
    _rule("labelled_id", _LABELLED_ID, "[id]", fit=_fit_id),
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
    """Return the personal-data spans in `text`, sorted by start and never overlapping.

    Earlier rules win. A later match that overlaps a taken span is dropped, unless its
    rule has a `fit` and it starts before that span: then it is cut back to the part
    before the span and kept if `fit` still accepts it.
    """
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
            if i > 0 and taken[i - 1].end > start:
                continue
            if i < len(taken) and taken[i].start < end:
                if rule.fit is None:
                    continue
                end = taken[i].start
            if rule.fit:
                fitted = rule.fit(scan, start, end)
                if fitted is None or fitted <= start:
                    continue
                end = fitted
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
MERCHANT_LINES = 3


def _merchant_name(line: str) -> str:
    if cut := _MERCHANT_CUT.search(line):
        line = line[: cut.start()]
    end = len(line)
    while end and (line[end - 1].isspace() or line[end - 1] in _MERCHANT_TRAILING):
        end -= 1
    return line[:end].lstrip()


def clean_merchant(text: str | None) -> str | None:
    """Keep the store name from the model's `merchant`, without the header around it.

    Looks at the first `MERCHANT_LINES` non-blank lines in order. Each is cut before the
    first postcode, phone number, URL, e-mail or phone label, and stripped of whitespace
    and trailing punctuation; the first one with text left is redacted and returned
    (`Tel. 0231 123456\\nREWE` -> `REWE`). Returns `None` if none has text left.
    """
    if text is None:
        return None
    lines = (line for line in text.splitlines() if line.strip())
    for _, line in zip(range(MERCHANT_LINES), lines, strict=False):
        if name := _merchant_name(line):
            return redact_text(name)
    return None

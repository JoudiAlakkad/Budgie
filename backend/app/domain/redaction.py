"""Deterministic removal of personal data from model output (decision 0017).

The response schema (`ai/schema.py`) is the first line of defence: it fixes every key,
type and enum, so free text reaches only three places: the line-item descriptions (where
payment and terminal lines slip in), `merchant` (where the receipt header slips in) and
the stored raw output. This module is the safety net for those three.

- `redact_text(text)` runs a small, ordered rule table: IBAN (mod-97 checksum), masked
  IBAN, card number (Luhn), masked card digits and labelled ids (terminal, trace,
  receipt, till, TSE, ...). Earlier rules win
  on overlap; a `labelled_id` value that runs into an earlier finding is cut back to the
  part before it (`Bon 12 <card>` -> `Bon [id] [card]`). A rule with a `value` group
  keeps its label and replaces only the value. Placeholders never match a rule, so
  redaction is idempotent. Rules see JSON escapes decoded, and no match crosses a `"` or
  splits an escape, so redacting the raw JSON output keeps it valid.
- `clean_merchant(text)` keeps the first line of `merchant` that still holds a name once
  the receipt header's contact block (postcode, phone, URL, e-mail, phone label) is cut
  off, then redacts what is left.

Bounded input: all three functions look at no more than `MAX_TEXT_CHARS` characters (see
`_bound`). The input is model output capped by `LLM_MAX_TOKENS` (about 8-10 KB), so the
bound is far above any real answer; it only limits the work a hostile string can cause.
A longer text is cut there (a little earlier if the cut would split a JSON escape, a card
number or an IBAN) and gets the `TRUNCATED` suffix. Nothing after the cut is returned, so
no unredacted text leaks past the limit.

Linear time: no regex here has a repeat that can split its input in more than one way,
and `labelled_id` has no lookahead: it matches label, separator and value run, and
`_fit_id` judges the value in Python. The `iban` rule's lookahead is bounded to 43
characters per word start, and `_fit_iban` judges it with the mod-97 checksum.
`finditer` goes on after every match, accepted or not. tests/unit/test_redaction.py
runs every rule on hostile strings of `MAX_TEXT_CHARS` characters (runs of each
character class, IBAN-shaped groups, every label joined by `/`, `.`, `=` and `-`) under a
fixed limit that only an exponential rule can reach.
"""

import re
from bisect import bisect_left, bisect_right
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


_ASCII_DIGITS = frozenset("0123456789")
_IBAN_CHARS = re.compile(r"[A-Z0-9]{5,}+")


def _mod97(chars: str, remainder: int = 0) -> int:
    """Extend the ISO 13616 remainder by `chars`: ASCII digits, and letters A=10 ... Z=35.

    One step per character, so the cost is linear and no big integer is built.
    """
    for char in chars:
        if char in _ASCII_DIGITS:
            remainder = (remainder * 10 + ord(char) - 48) % 97
        else:
            remainder = (remainder * 100 + ord(char) - 55) % 97
    return remainder


def iban_checksum_valid(iban: str) -> bool:
    """True if `iban` passes the ISO 13616 mod-97 check; single spaces are ignored.

    The first four characters (country code and check digits) move to the end, letters
    become 10 ... 35, and the number must leave remainder 1 when divided by 97. Length
    and shape are not checked here.
    """
    compact = iban.replace(" ", "")
    if _IBAN_CHARS.fullmatch(compact) is None:
        return False
    return _mod97(compact[:4], _mod97(compact[4:])) == 1


# IBAN lengths of the countries a German receipt most likely shows (SWIFT IBAN registry).
# Other country codes only need 15-34 characters; the checksum is the main guard.
IBAN_LENGTHS: dict[str, int] = {
    "AD": 24, "AT": 20, "BE": 16, "BG": 22, "CH": 21, "CY": 28, "CZ": 24, "DE": 22,
    "DK": 18, "EE": 20, "ES": 24, "FI": 18, "FR": 27, "GB": 22, "GR": 27, "HR": 21,
    "HU": 28, "IE": 22, "IS": 26, "IT": 27, "LI": 21, "LT": 20, "LU": 20, "LV": 21,
    "MC": 27, "MT": 31, "NL": 18, "NO": 15, "PL": 28, "PT": 25, "RO": 24, "SE": 24,
    "SI": 19, "SK": 24, "SM": 27, "TR": 26,
}  # fmt: skip
_IBAN_MIN, _IBAN_MAX = 15, 34
_WORD = re.compile(r"\w")


def _fit_iban(text: str, start: int, end: int) -> int | None:
    """The end of the longest IBAN in `text[start:end]`, or None.

    The span is the 4-character head (country code, check digits), then either one
    compact run or space-separated groups. A candidate ends after a group, never before a
    word character, has 15-34 characters (the country's length if it is in
    `IBAN_LENGTHS`) and passes the mod-97 check. Trailing groups may be dropped, so a
    word after a spaced IBAN (`... 3201 BANK`) is not part of it. The span is at most 43
    characters, and the remainder is carried group by group, so this is constant time.
    """
    head = text[start : start + 4]
    want = IBAN_LENGTHS.get(head[:2])
    longest = want or _IBAN_MAX
    remainder = 0
    length = 4
    position = start + 4
    best = None
    for group in text[position:end].split(" "):  # the regex let only [A-Z0-9] through
        position += len(group) + 1
        if not group:  # the space after the head of a spaced IBAN
            continue
        length += len(group)
        if length > longest:  # the length only grows, so no later group can fit
            break
        remainder = _mod97(group, remainder)
        group_end = position - 1
        if (
            _IBAN_MIN <= length <= _IBAN_MAX
            and (want is None or length == want)
            and _WORD.match(text, group_end) is None
            and _mod97(head, remainder) == 1
        ):
            best = group_end
    return best


_MASK = frozenset("*Xx#")


def _is_masked_iban(match: str) -> bool:
    """A masked IBAN hides at least four characters and ends in a shown digit."""
    return sum(char in _MASK for char in match[4:]) >= 4 and match[-1] in _ASCII_DIGITS


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


# An IBAN candidate: country code and check digits, then one compact run or 2-7 groups of
# four after single spaces and an optional short last group. Every repeat is possessive
# and bounded (at most 43 characters), and `_fit_iban` judges it. The candidate sits in a
# lookahead, so `finditer` tries every word start: a rejected candidate (`XX12 DE89 ...`)
# doesn't hide an IBAN that starts inside it.
_IBAN = (
    r"\b(?=(?P<value>[A-Z]{2}[0-9]{2}"
    r"(?:[A-Z0-9]{11,30}+|(?:[ ][A-Z0-9]{4}){2,7}+(?:[ ][A-Z0-9]{1,3}+)?+)))"
)
# A masked IBAN on a direct-debit receipt: country code and check digits, then a compact
# run of shown digits, mask characters and shown digits (`DE89XXXXXXXXXXXXXX3000`), or
# groups of four after single spaces (`DE89 **** **** **** **30 00`). `_is_masked_iban`
# wants four mask characters and a shown digit at the end.
_IBAN_MASKED = (
    r"\b[A-Z]{2}[0-9]{2}"
    r"(?:[0-9]*+[*Xx#]{4,}+[0-9]{2,6}+|(?:[ ][0-9*Xx#]{4}){2,7}+(?:[ ][0-9*Xx#]{1,3}+)?+)"
    r"(?![\w*#])"
)

RULES: tuple[Rule, ...] = (
    _rule("iban", _IBAN, "[iban]", fit=_fit_iban),
    _rule("iban_masked", _IBAN_MASKED, "[iban]", _is_masked_iban),
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


# The most characters the functions here look at. Model output is capped by
# `LLM_MAX_TOKENS` (2048 tokens, about 8-10 KB), so a real answer is never cut.
MAX_TEXT_CHARS = 65_536
# Appended to a text that was cut at MAX_TEXT_CHARS. Not a rule placeholder: no rule
# matches it, and it never stands for personal data.
TRUNCATED = " [truncated]"
_LONGEST_ESCAPE = 6  # `\uXXXX`
# The longest `iban` candidate: head and seven groups of four, each after a space, and a
# last group (4 + 35 + 4); a `card` candidate has at most 27 characters.
_LONGEST_NUMBER = 43
# The run of card and IBAN characters at the end of the searched window. `search` starts
# at the leftmost position, so it finds the whole run (the window is bounded).
_NUMBER_TAIL = re.compile(r"[0-9A-Z -]+\Z")
_LONGEST_PLACEHOLDER = max(len(placeholder) for placeholder in PLACEHOLDERS)


def _size(text: str) -> int:
    """The length of `text`, with each placeholder counted as one character.

    Redaction can make a text longer (`Bon.1,` -> `Bon.[id],`), but never larger by this
    measure: a finding never touches `[` or `]`, so the placeholders already in the text
    survive, and each new one replaces at least one character. Placeholders can't overlap
    each other (each holds one `[`, at its start), so `str.count` finds each one.
    """
    if len(text) <= MAX_TEXT_CHARS:
        return len(text)
    if len(text) > _LONGEST_PLACEHOLDER * MAX_TEXT_CHARS:
        return len(text)  # over the limit by any measure: no need to count
    return len(text) - sum(text.count(p) * (len(p) - 1) for p in PLACEHOLDERS)


def _cut(text: str) -> str:
    """The first MAX_TEXT_CHARS characters of `text`, or fewer: no JSON escape is split,
    and no card number or IBAN is cut in two.

    `_unescape` gives the offset of every decoded character; the cut goes at one of them,
    so never inside an escape. An escape that starts before the limit ends within
    `_LONGEST_ESCAPE` characters, so only that much more is decoded.

    A card number or IBAN that runs over the limit would lose its checksum and survive
    with most of its digits (`4111 1111 1111 111|1`). Both are at most `_LONGEST_NUMBER`
    characters of `[0-9A-Z -]`, so the cut moves back over such a run at the end, at most
    that far. A cut-off labelled id keeps its label and is still redacted.
    """
    scan, offsets = _unescape(text[: MAX_TEXT_CHARS + _LONGEST_ESCAPE])
    end = bisect_right(offsets, MAX_TEXT_CHARS) - 1  # decoded characters before the limit
    if (tail := _NUMBER_TAIL.search(scan, max(end - _LONGEST_NUMBER, 0), end)) is not None:
        end = tail.start()
    return text[: offsets[end]]


def _bound(text: str) -> tuple[str, bool]:
    """The part of `text` to process, and whether the result gets the TRUNCATED suffix.

    A text that ends in TRUNCATED keeps it, and the part before it is processed. That
    part, or the whole text, is cut at MAX_TEXT_CHARS if its `_size` is over the limit.

    Idempotent: a cut part has at most MAX_TEXT_CHARS characters, and redacting it doesn't
    raise its `_size`, so a second pass finds the suffix and a part within the limit, and
    doesn't cut again. A result without the suffix came from a text without it, of a
    `_size` within the limit, so it stays within the limit.
    """
    marked = text.endswith(TRUNCATED)
    body = text[: -len(TRUNCATED)] if marked else text
    if _size(body) > MAX_TEXT_CHARS:
        return _cut(body), True
    return body, marked


def find_personal_data(text: str) -> list[Finding]:
    """Return the personal-data spans in `text`, sorted by start and never overlapping.

    Earlier rules win. A later match that overlaps a taken span is dropped, unless its
    rule has a `fit` and it starts before that span: then it is cut back to the part
    before the span and kept if `fit` still accepts it.

    Only the part `redact_text` keeps is searched (`_bound`): nothing after the cut of a
    text longer than MAX_TEXT_CHARS.
    """
    return _find(_bound(text)[0])


def _find(text: str) -> list[Finding]:
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
    """Replace every finding with its typed placeholder. Idempotent.

    A text longer than MAX_TEXT_CHARS is cut there first, and the result ends in
    TRUNCATED (`_bound`).
    """
    body, marked = _bound(text)
    return _redact(body) + (TRUNCATED if marked else "")


def _redact(text: str) -> str:
    parts: list[str] = []
    last = 0
    for finding in _find(text):
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

    A text longer than MAX_TEXT_CHARS is cut there first (`_bound`). If the name comes
    from the line the cut ends, it gets the TRUNCATED suffix.
    """
    if text is None:
        return None
    body, marked = _bound(text)
    lines = body.splitlines()
    shown = (i for i, line in enumerate(lines) if line.strip())
    for _, i in zip(range(MERCHANT_LINES), shown, strict=False):
        if name := _merchant_name(lines[i]):
            cut = marked and i == len(lines) - 1 and body.endswith(lines[i])
            return _redact(name) + (TRUNCATED if cut else "")
    return None

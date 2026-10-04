"""Independent personal-data scan for recorded-response fixtures (decision 0017).

`app.domain.redaction` can only prove that redacting again changes nothing. This scan
shares no code with it: it is deliberately broader, and everything it may see is in the
allowlist. It also covers what the app rules leave to the schema and `clean_merchant`
(addresses, URLs, phone numbers), so a fixture can't carry them past the generator.

Used by scripts/make_fixtures.py, which refuses to write on a hit, and by the tests
(tests/unit/fixture_shape.py), which run it on every committed fixture. Only pattern
names are reported: the matched text may itself be personal data.

Every pattern is bounded, anchored to the start of a run, or possessive, so a scan is
linear in the text.
"""

import re

# Removed before scanning. Each entry is something the fixtures legitimately hold.
KNOWN_PLACEHOLDERS = ("[card]", "[iban]", "[id]")
ALLOWLIST = [
    # placeholders written by the redaction (test_recorded_fixtures checks the list)
    *(re.escape(placeholder) for placeholder in KNOWN_PLACEHOLDERS),
    # dates and times: 2026-09-17, 17.09.2026, 14:32(:05)
    r"\b\d{4}-\d{2}-\d{2}\b",
    r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b",
    r"\b\d{1,2}:\d{2}(?::\d{2})?\b",
    # prices and amounts as JSON numbers or receipt text: 7.39, -0.25, 1.234,56
    r"-?\b\d{1,3}(?:\.\d{3})*,\d{2}\b",
    r"-?\b\d+\.\d{1,2}\b",
]
_ALLOWED = re.compile("|".join(f"(?:{pattern})" for pattern in ALLOWLIST))

_STREET_SUFFIX = r"weg|platz|allee|gasse|ring|damm|str\.|straße|strasse"
_TLD = r"de|com|net|org|eu|info|shop|example|io"
SUSPICIOUS = {
    # ids, card or phone numbers. Token counts live in `usage`, which isn't scanned.
    "5+ digits": r"\d{5,}",
    "at sign": r"@",
    "masked digits": r"[*Xx#]{3,}[ -]?\d",
    "street": r"(?i:str\.|straße|strasse)",
    # `Lindenweg 4`, `Musterstraße 12a`, `Am Markt 3`, `An der Ruhrallee 5`
    "street and number": (
        rf"(?i:(?:{_STREET_SUFFIX})[ \t]*+\d)"
        r"|\b(?:Am|An der|Im)[ ]++[A-ZÄÖÜ][\w-]*+[ ]++\d"
    ),
    # `https://…`, `www.…`, and a bare domain with a common TLD (`beispiel-markt.de`)
    "url": rf"(?i:https?://|www\.|(?<![\w-])[\w-]++\.(?:{_TLD})\b)",
    # `0231 9876`, `0231/123456`, `02 31-1234`: an area code and a number
    "phone": r"(?<![\d.,])0\d{2,5}[ /-]\d{3,}",
    # A label alone is fine (the prompts name the payment lines, and older redactions
    # kept `Tel. [phone]`); a label followed by a digit within a short stretch is not.
    "contact or tax label with a value": (
        r"(?i:\b(?:Tel(?:efon)?|Fon|Fax|USt)\b[^\n\"\d\[]{0,20}\d)"
    ),
}
_SUSPICIOUS = {name: re.compile(pattern) for name, pattern in SUSPICIOUS.items()}


def suspicious(text: str) -> list[str]:
    """Names of the suspicious patterns left in `text` once the allowlist is removed."""
    rest = _ALLOWED.sub(" ", text)
    return sorted(name for name, pattern in _SUSPICIOUS.items() if pattern.search(rest))


def texts(data: dict) -> list[str]:
    """Every message content and error message in a fixture."""
    found = []
    for response in data["responses"]:
        body = response["body"]
        if "error" in body:
            found.append(body["error"]["message"])
        else:
            found += [choice["message"]["content"] for choice in body["choices"]]
    return found


def suspicious_texts(data: dict) -> list[str]:
    """The suspicious pattern names found in any text of a fixture."""
    return sorted({name for text in texts(data) for name in suspicious(text)})

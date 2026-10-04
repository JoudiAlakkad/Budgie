"""Shape of a recorded-response fixture written by scripts/make_fixtures.py."""

import re

from app.domain.redaction import find_personal_data

USAGE_KEYS = {"prompt_tokens", "completion_tokens", "total_tokens"}


def assert_fixture_shape(name: str, data: dict) -> None:
    """Fail unless `data` is a valid fixture; `name` is the case name (file stem)."""
    assert set(data) == {"description", "source", "max_tokens", "responses"}
    assert isinstance(data["description"], str) and data["description"]
    assert isinstance(data["source"], str) and data["source"]
    assert isinstance(data["max_tokens"], int)
    assert data["responses"]
    for n, response in enumerate(data["responses"], start=1):
        assert set(response) == {"status_code", "body"}
        body = response["body"]
        if response["status_code"] != 200:
            assert body == {
                "error": {
                    "message": body["error"]["message"],
                    "type": "api_error",
                    "param": None,
                    "code": None,
                }
            }
            continue
        assert set(body) == {"id", "object", "created", "model", "choices", "usage"}
        assert body["id"] == f"chatcmpl-{name}-{n}"
        assert (body["object"], body["created"], body["model"]) == (
            "chat.completion",
            1790000000,
            "gemma3:4b",
        )
        assert set(body["usage"]) == USAGE_KEYS
        [choice] = body["choices"]
        assert choice["index"] == 0
        assert choice["message"]["role"] == "assistant"
        assert isinstance(choice["message"]["content"], str)
        cut_off = body["usage"]["completion_tokens"] >= data["max_tokens"]
        assert choice["finish_reason"] == ("length" if cut_off else "stop")


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


def personal_data(data: dict) -> list[str]:
    """The kinds of personal data left in a fixture's texts."""
    return sorted({f.kind for text in texts(data) for f in find_personal_data(text)})


# ---------------------------------------------------------------- independent scan
# `personal_data` uses the redaction rules themselves, so it can only prove that
# redacting again changes nothing. This scan shares no code with app.domain.redaction:
# it is deliberately broader, and everything it may see is listed in the allowlist.

# Removed before scanning. Each entry is something the fixtures legitimately hold.
KNOWN_PLACEHOLDERS = ("[address]", "[card]", "[email]", "[iban]", "[id]", "[name]", "[phone]")
KNOWN_PLACEHOLDERS += ("[taxid]", "[url]")
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

SUSPICIOUS = {
    # ids, card or phone numbers. Token counts live in `usage`, which isn't scanned.
    "5+ digits": r"\d{5,}",
    "at sign": r"@",
    "masked digits": r"[*Xx#]{3,}[ -]?\d",
    "street": r"(?i:str\.|straße|strasse)",
    # A label alone is fine (the prompts and the redaction keep `Tel. [phone]`); a label
    # followed by a digit within the same short stretch is not.
    "contact or tax label with a value": r"(?i:\b(?:Tel|Fax|USt)\b[^\n\"\d\[]{0,20}\d)",
}
_SUSPICIOUS = {name: re.compile(pattern) for name, pattern in SUSPICIOUS.items()}


def suspicious(text: str) -> list[str]:
    """Names of the suspicious patterns left in `text` once the allowlist is removed."""
    rest = _ALLOWED.sub(" ", text)
    return sorted(name for name, pattern in _SUSPICIOUS.items() if pattern.search(rest))


def suspicious_texts(data: dict) -> list[str]:
    """The suspicious pattern names found in any text of a fixture."""
    return sorted({name for text in texts(data) for name in suspicious(text)})

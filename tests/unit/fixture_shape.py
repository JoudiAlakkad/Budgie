"""Shape of a recorded-response fixture written by scripts/make_fixtures.py."""

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

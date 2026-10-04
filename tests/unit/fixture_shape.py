"""Shape of a recorded-response fixture written by scripts/make_fixtures.py.

The independent personal-data scan lives in scripts/fixture_scan.py, so the generator
runs the same scan before it writes. It is loaded here by path (scripts/ isn't a
package) and registered as `fixture_scan`, the name scripts/make_fixtures.py imports.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from app.domain.redaction import find_personal_data

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
USAGE_KEYS = {"prompt_tokens", "completion_tokens", "total_tokens"}


def load_script(name: str) -> ModuleType:
    """Import `scripts/<name>.py` once, under its own module name."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fixture_scan = load_script("fixture_scan")
KNOWN_PLACEHOLDERS: tuple[str, ...] = fixture_scan.KNOWN_PLACEHOLDERS
SUSPICIOUS: dict[str, str] = fixture_scan.SUSPICIOUS
suspicious = fixture_scan.suspicious
suspicious_texts = fixture_scan.suspicious_texts
texts = fixture_scan.texts


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


def personal_data(data: dict) -> list[str]:
    """The kinds of personal data left in a fixture's texts."""
    return sorted({f.kind for text in texts(data) for f in find_personal_data(text)})

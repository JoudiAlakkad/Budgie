"""Replay the recorded model responses in tests/fixtures/recorded_responses/.

A case is `{description, source, max_tokens, responses: [{status_code, body}]}`
(written by scripts/make_fixtures.py, never by hand). `replay` serves the responses
in order, one per HTTP call, and fails the test on any extra call.
"""

import json
from pathlib import Path

import httpx
import pytest

from tests.unit.fixture_shape import assert_fixture_shape

RECORDED = Path(__file__).resolve().parent / "fixtures" / "recorded_responses"


def case_names() -> list[str]:
    return sorted(path.stem for path in RECORDED.glob("*.json"))


def load_case(name: str) -> dict:
    """Load and shape-check one recorded case by its file stem."""
    data = json.loads((RECORDED / f"{name}.json").read_text(encoding="utf-8"))
    assert_fixture_shape(name, data)
    return data


def replay(case: dict) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """A transport serving `case["responses"]` in order, and the requests it received."""
    responses = list(case["responses"])
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) > len(responses):
            pytest.fail(f"unexpected model call {len(seen)}; the case has {len(responses)}")
        response = responses[len(seen) - 1]
        return httpx.Response(response["status_code"], json=response["body"])

    return httpx.MockTransport(handler), seen

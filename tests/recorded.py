"""Replay the recorded model responses in tests/fixtures/recorded_responses/.

A case is `{description, source, max_tokens, responses: [{status_code, body}]}`
(written by scripts/make_fixtures.py, never by hand). `replay` serves the responses
in order, one per HTTP call, and fails the test on any extra call.
"""

import base64
import json
from pathlib import Path

import httpx
import pytest

from app.ai.prompts import load_prompts
from app.ai.schema import EXAMPLE_JSON, EXAMPLE_JSON_V1
from tests.unit.fixture_shape import assert_fixture_shape

RECORDED = Path(__file__).resolve().parent / "fixtures" / "recorded_responses"
MIN_DISTINCTIVE = 6  # shorter values ("BROT", "ALDI") could appear by chance


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


def distinctive_values(case: dict, image: bytes) -> set[str]:
    """Values from a case that must never reach a log: answers, merchants, items, errors.

    Also the image (base64, as sent), the prompts and the example answer.
    """
    values = {base64.b64encode(image).decode("ascii"), EXAMPLE_JSON, EXAMPLE_JSON_V1}
    values.add('"Beispiel Markt"')
    for version in ("v1", "v2"):
        prompts = load_prompts(version)
        values |= {prompts.system.splitlines()[0], prompts.user.strip(), prompts.repair}
    for response in case["responses"]:
        if response["status_code"] != 200:
            values.add(response["body"]["error"]["message"])
            continue
        content = response["body"]["choices"][0]["message"]["content"]
        values.add(content.strip())
        try:
            data = json.loads(content.strip().removeprefix("```json").removesuffix("```"))
        except ValueError:
            continue
        if isinstance(data, dict):
            values.add(str(data.get("merchant") or ""))
            for entry in data.get("line_items") or []:
                if isinstance(entry, dict):
                    values.add(str(entry.get("description") or ""))
    return {value for value in values if len(value) >= MIN_DISTINCTIVE}

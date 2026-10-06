"""Shared helpers for the receipt and expense API tests."""

import datetime as dt
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.ai.client import LLMClient
from app.config import Settings
from app.db.records import ReceiptRecord
from app.db.repositories.receipts import ReceiptRepository
from app.services.dependencies import database_for
from tests.recorded import replay

# The smallest byte strings that pass the upload's type check (first bytes only).
JPEG = b"\xff\xd8\xff\xe0fake-jpeg-bytes\x00\x01"
PNG = b"\x89PNG\r\n\x1a\nfake-png"
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 fake"

TODAY = dt.date(2026, 10, 5)


def upload(
    client: TestClient,
    data: bytes = JPEG,
    filename: str = "receipt.jpg",
    content_type: str = "image/jpeg",
) -> httpx.Response:
    return client.post("/api/receipts", files={"file": (filename, data, content_type)})


class NoopPipeline:
    """Leaves every receipt `uploaded`, for tests that don't care about extraction."""

    def run(self, receipt_id: int) -> None:
        pass


def completion(content: str, finish_reason: str = "stop", tokens: int = 180) -> dict[str, Any]:
    """An OpenAI-compatible chat completion body."""
    return {
        "id": "chatcmpl-inline",
        "object": "chat.completion",
        "created": 1790000000,
        "model": "gemma3:4b",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 312, "completion_tokens": tokens, "total_tokens": 312 + tokens},
    }


def answer(**fields: Any) -> str:
    """A synthetic, schema-valid answer; `fields` override the defaults."""
    data: dict[str, Any] = {
        "is_receipt": True,
        "merchant": "Beispiel Markt",
        "date": "2026-10-01",
        "currency": "EUR",
        "line_items": [{"description": "BROT", "qty": 1, "unit_price": 2.49, "amount": 2.49}],
        "subtotal": None,
        "tax": None,
        "total": 2.49,
        "payment_method": "cash",
        "unreadable_fields": [],
    }
    return json.dumps(data | fields, ensure_ascii=False)


def inline_case(*contents: str) -> dict[str, Any]:
    """A case in the recorded shape, for answers no fixture covers."""
    return {
        "description": "inline",
        "source": "synthetic, inline in the test",
        "max_tokens": 2048,
        "responses": [{"status_code": 200, "body": completion(c)} for c in contents],
    }


def answers(case: dict[str, Any]) -> list[str]:
    return [
        r["body"]["choices"][0]["message"]["content"]
        for r in case["responses"]
        if r["status_code"] == 200
    ]


class ModelServer:
    """The model server behind a real `LLMClient`; each test sets what it answers."""

    def __init__(self) -> None:
        self.transport: httpx.BaseTransport = httpx.MockTransport(self._unexpected)
        self.during_call: Callable[[], None] | None = None

    @staticmethod
    def _unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail("unexpected model call")

    def client(self) -> LLMClient:
        return LLMClient(
            base_url="http://model-server/v1",
            api_key="ollama",
            model="gemma3:4b",
            timeout=120,
            transport=self.transport,
            max_retries=1,
        )

    def serve(self, case: dict[str, Any]) -> list[httpx.Request]:
        """Replay `case`; returns the requests the model server receives."""
        transport, seen = replay(case)
        self.transport = self._hooked(transport)
        return seen

    def fail_with(self, error: type[httpx.TransportError]) -> list[httpx.Request]:
        """Raise `error` on every call (unreachable server, timeout)."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            raise error("simulated", request=request)

        self.transport = self._hooked(httpx.MockTransport(handler))
        return seen

    def _hooked(self, transport: httpx.MockTransport) -> httpx.MockTransport:
        inner = transport.handler

        def handler(request: httpx.Request) -> httpx.Response:
            if self.during_call is not None:
                self.during_call()
            return inner(request)  # type: ignore[no-any-return,operator]

        return httpx.MockTransport(handler)


def stored(settings: Settings, receipt_id: int) -> ReceiptRecord:
    """The receipt row, including the columns the API doesn't show."""
    with database_for(settings.database_url).transaction() as session:
        record = ReceiptRepository(session).get(receipt_id)
    assert record is not None
    return record

"""Turn a receipt image into a validated `ReceiptExtraction` (docs/wiki/backend/ai-extraction.md).

One call, then at most one repair call. Output cut off at `max_tokens` is
`MalformedOutput` without a repair; `is_receipt=false` is `NotAReceipt`. The
extractor does not redact: `raw_output` is the model's own text, and the pipeline
redacts it before storing (decision 0017). Nothing from the prompts, the image or
the model's answer is logged.
"""

import base64
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from app.ai.client import ChatCompletion
from app.ai.prompts import Prompts
from app.ai.schema import RESPONSE_FORMAT, ReceiptExtraction
from app.errors import ExtractionError, MalformedOutput, NotAReceipt

logger = logging.getLogger(__name__)

MAX_ERROR_LINES = 10
_FENCE = re.compile(r"^```[A-Za-z]*\s*|\s*```$")


class ChatClient(Protocol):
    """What the extractor needs from `LLMClient`; tests may pass a fake."""

    model: str

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int,
        response_format: dict[str, Any] | None = None,
    ) -> ChatCompletion: ...


@dataclass(frozen=True)
class ExtractionResult:
    extraction: ReceiptExtraction
    model: str
    prompt_version: str
    latency_s: float
    raw_output: str  # the accepted answer, unredacted; never log it
    repaired: bool
    completion_tokens: int | None  # summed over the calls; None if the server didn't say


class InvalidOutput(Exception):
    """The answer isn't a valid `ReceiptExtraction`; `reason` goes into the repair prompt."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _load_json(raw: str) -> object:
    text = _FENCE.sub("", raw.strip())
    if not text:
        raise InvalidOutput("The answer is empty.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        first_error = exc
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    # The decoder's message names a position, never the text itself.
    raise InvalidOutput(f"Invalid JSON: {first_error.msg} at line {first_error.lineno}")


def _validation_lines(exc: ValidationError) -> str:
    errors = exc.errors(include_url=False, include_input=False, include_context=False)
    lines = [
        f"{'.'.join(str(part) for part in err['loc']) or '(root)'}: {err['msg']}" for err in errors
    ]
    if len(lines) > MAX_ERROR_LINES:
        more = len(lines) - (MAX_ERROR_LINES - 1)
        lines = [*lines[: MAX_ERROR_LINES - 1], f"... and {more} more errors"]
    return "\n".join(lines)


def parse_output(raw: str) -> ReceiptExtraction:
    """Parse and validate the model's answer; raises `InvalidOutput`."""
    data = _load_json(raw)
    if not isinstance(data, dict):
        raise InvalidOutput("The answer must be one JSON object.")
    try:
        return ReceiptExtraction.model_validate(data)
    except ValidationError as exc:
        raise InvalidOutput(_validation_lines(exc)) from None


def _says_not_a_receipt(raw: str) -> bool:
    """True if the answer is a JSON object with `is_receipt: false`, valid or not."""
    try:
        data = _load_json(raw)
    except InvalidOutput:
        return False
    return isinstance(data, dict) and data.get("is_receipt") is False


class Extractor:
    def __init__(
        self,
        client: ChatClient,
        prompts: Prompts,
        *,
        temperature: float,
        max_tokens: int,
    ) -> None:
        self._client = client
        self._prompts = prompts
        self._temperature = temperature
        self._max_tokens = max_tokens

    @property
    def model(self) -> str:
        return self._client.model

    @property
    def prompt_version(self) -> str:
        return self._prompts.version

    def extract(self, image: bytes, mime: str) -> ExtractionResult:
        """Extract one receipt; raises an `ExtractionError` subclass on failure."""
        started = time.perf_counter()
        try:
            result = self._extract(image, mime, started)
        except ExtractionError as exc:
            exc.latency_s = time.perf_counter() - started
            logger.info(
                "Extraction failed: %s after %.1f s (prompt %s)",
                exc.code,
                exc.latency_s,
                self.prompt_version,
            )
            raise
        logger.info(
            "Extraction ok after %.1f s (prompt %s, repaired %s)",
            result.latency_s,
            self.prompt_version,
            result.repaired,
        )
        return result

    def _complete(self, messages: list[dict[str, Any]]) -> ChatCompletion:
        return self._client.chat_completion(
            messages,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            response_format=RESPONSE_FORMAT,
        )

    def _extract(self, image: bytes, mime: str, started: float) -> ExtractionResult:
        data_url = f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._prompts.system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": self._prompts.user},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ]

        first = self._complete(messages)
        first_raw = first.content or ""
        if first.cut_off:
            raise MalformedOutput(
                f"Output cut off at {self._max_tokens} tokens ({first.finish_reason})",
                raw_output=first_raw,
                attempts=(first_raw,),
            )
        try:
            extraction = parse_output(first_raw)
        except InvalidOutput as invalid:
            if _says_not_a_receipt(first_raw):
                raise NotAReceipt("is_receipt=false", raw_output=first_raw) from None
            reason = invalid.reason
        else:
            return self._result(extraction, first_raw, started, False, first)

        repair = self._prompts.repair.replace("{errors}", reason)
        second = self._complete(
            [
                *messages,
                {"role": "assistant", "content": first_raw},
                {"role": "user", "content": repair},
            ]
        )
        second_raw = second.content or ""
        attempts = (first_raw, second_raw)
        if second.cut_off:
            raise MalformedOutput(
                f"Repair output cut off at {self._max_tokens} tokens ({second.finish_reason})",
                raw_output=second_raw,
                attempts=attempts,
            )
        try:
            extraction = parse_output(second_raw)
        except InvalidOutput as invalid:
            raise MalformedOutput(
                f"Invalid after one repair: {invalid.reason}",
                raw_output=second_raw,
                attempts=attempts,
            ) from None
        return self._result(extraction, second_raw, started, True, first, second)

    def _result(
        self,
        extraction: ReceiptExtraction,
        raw: str,
        started: float,
        repaired: bool,
        *completions: ChatCompletion,
    ) -> ExtractionResult:
        if not extraction.is_receipt:
            raise NotAReceipt("is_receipt=false", raw_output=raw)
        tokens = [c.completion_tokens for c in completions if c.completion_tokens is not None]
        return ExtractionResult(
            extraction=extraction,
            model=self.model,
            prompt_version=self.prompt_version,
            latency_s=time.perf_counter() - started,
            raw_output=raw,
            repaired=repaired,
            completion_tokens=sum(tokens) if tokens else None,
        )

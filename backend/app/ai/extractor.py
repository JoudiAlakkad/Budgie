"""Turn a receipt image into a validated `ReceiptExtraction` (docs/wiki/backend/ai-extraction.md).

One call, then at most one repair call. Output cut off at `max_tokens` is
`MalformedOutput` without a repair; `is_receipt=false` is `NotAReceipt`, in either
answer and even when the rest of that answer is invalid. The
extractor does not redact: `raw_output` is the model's own text, and the pipeline
redacts it before storing (decision 0017). Nothing from the prompts, the image or
the model's answer is logged. The system prompt's `{example}` is filled here with
the example JSON of the prompt version's output spec (`system_prompt`), so `ai.prompts`
stays plain text.

Each prompt version has its own output spec (`schema.OUTPUT_SPECS`): the request sends
its `response_format`, and the answer is validated against its model. A valid answer
to another model than `ReceiptExtraction` (the frozen v1) is then converted to
`ReceiptExtraction`, so `ExtractionResult.extraction` always has the current shape;
`raw_output` stays what the model sent.
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
from app.ai.prompts import Prompts, UnknownPromptVersion
from app.ai.schema import OUTPUT_SPECS, OutputSpec, ReceiptExtraction
from app.errors import ExtractionError, MalformedOutput, NotAReceipt

logger = logging.getLogger(__name__)

MAX_ERROR_LINES = 10
EXAMPLE_PLACEHOLDER = "{example}"
_OPENING_FENCE = re.compile(r"```[A-Za-z]*\s*")


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


class MissingOutputSpec(UnknownPromptVersion):
    """A prompt version without an entry in `OUTPUT_SPECS`.

    A subclass of `UnknownPromptVersion`, so it fails like an unknown version: the
    extractor can't be built, and the pipeline fails the receipt with `interrupted`.
    """


def output_spec(version: str) -> OutputSpec:
    """The output spec of a prompt version; raises `MissingOutputSpec`."""
    try:
        return OUTPUT_SPECS[version]
    except KeyError:
        raise MissingOutputSpec(
            f"Prompt version {version!r} has no output spec; "
            f"specs exist for: {', '.join(sorted(OUTPUT_SPECS))}"
        ) from None


class InvalidOutput(Exception):
    """The answer isn't a valid `ReceiptExtraction`; `reason` goes into the repair prompt."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def strip_fence(raw: str) -> str:
    """The answer without surrounding whitespace and a Markdown code fence.

    Linear time: the opening fence is matched once at the start, and the closing one is
    a suffix test. (A `re.sub` with `\\s*```$` retried at every whitespace character
    and was quadratic on long runs of spaces.)
    """
    text = raw.strip()
    if opening := _OPENING_FENCE.match(text):
        text = text[opening.end() :]
    if text.endswith("```"):
        text = text[:-3].rstrip()
    return text


def load_json(raw: str) -> object:
    """The answer as JSON: fences stripped, else the slice from the first `{` to the last `}`.

    Raises `InvalidOutput`. The pipeline uses it too, so the raw output it stores is
    parsed exactly as the extractor parsed it.
    """
    text = strip_fence(raw)
    if not text:
        raise InvalidOutput("The answer is empty.")
    try:
        return json.loads(text)
    except (ValueError, RecursionError) as exc:
        # JSONDecodeError is a ValueError; a huge integer raises a plain ValueError and
        # deep nesting a RecursionError.
        first_error = exc
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start : end + 1])
        except (ValueError, RecursionError):
            pass
    raise InvalidOutput(_json_error(first_error))


def _json_error(exc: Exception) -> str:
    """Describe a decoding failure without quoting the text."""
    if isinstance(exc, json.JSONDecodeError):
        # The decoder's message names a position, never the text itself.
        return f"Invalid JSON: {exc.msg} at line {exc.lineno}"
    if isinstance(exc, RecursionError):
        return "Invalid JSON: nested too deeply"
    # e.g. an integer with more digits than int() accepts.
    return "Invalid JSON: a value can't be decoded (a number may have too many digits)"


def _validation_lines(exc: ValidationError) -> str:
    errors = exc.errors(include_url=False, include_input=False, include_context=False)
    lines = [
        f"{'.'.join(str(part) for part in err['loc']) or '(root)'}: {err['msg']}" for err in errors
    ]
    if len(lines) > MAX_ERROR_LINES:
        more = len(lines) - (MAX_ERROR_LINES - 1)
        lines = [*lines[: MAX_ERROR_LINES - 1], f"... and {more} more errors"]
    return "\n".join(lines)


def parse_output(raw: str, spec: OutputSpec | None = None) -> ReceiptExtraction:
    """Parse and validate the model's answer; raises `InvalidOutput`.

    The answer is validated against `spec.model` (default: the current version's
    `ReceiptExtraction`). An answer to another model is then converted to
    `ReceiptExtraction`: keys it doesn't know are ignored, and `subtotal`/`tax` leave
    `unreadable_fields`. A conversion error is invalid output too.
    """
    model = ReceiptExtraction if spec is None else spec.model
    data = load_json(raw)
    if not isinstance(data, dict):
        raise InvalidOutput("The answer must be one JSON object.")
    try:
        parsed = model.model_validate(data)
        if isinstance(parsed, ReceiptExtraction):
            return parsed
        return ReceiptExtraction.model_validate(parsed.model_dump())
    except ValidationError as exc:
        raise InvalidOutput(_validation_lines(exc)) from None


def system_prompt(prompts: Prompts) -> str:
    """The system prompt with `{example}` filled with the version's example JSON.

    It is filled here, not in `ai.prompts`, so the prompt loader stays free of the
    schema: the prompt files are text, and the example is built from the models.
    Raises `MissingOutputSpec` for a version without an output spec.
    """
    example = output_spec(prompts.version).example_json
    return prompts.system.replace(EXAMPLE_PLACEHOLDER, example)


def _says_not_a_receipt(raw: str) -> bool:
    """True if the answer is a JSON object with `is_receipt: false`, valid or not."""
    try:
        data = load_json(raw)
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
        # Raises MissingOutputSpec here, so a version without a spec fails the build.
        self._spec = output_spec(prompts.version)
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
            response_format=self._spec.response_format,
        )

    def _extract(self, image: bytes, mime: str, started: float) -> ExtractionResult:
        data_url = f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt(self._prompts)},
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
            extraction = parse_output(first_raw, self._spec)
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
            extraction = parse_output(second_raw, self._spec)
        except InvalidOutput as invalid:
            if _says_not_a_receipt(second_raw):
                raise NotAReceipt("is_receipt=false", raw_output=second_raw) from None
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

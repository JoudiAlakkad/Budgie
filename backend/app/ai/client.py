"""Client for an OpenAI-compatible model server (docs/wiki/backend/ai-extraction.md).

`chat_completion` maps every failure to a typed `ExtractionError`. Logs and error
reasons never contain message contents, image bytes, model output or the API key.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.errors import LLMError, LLMTimeout, LLMUnavailable, UnreadableImage

logger = logging.getLogger(__name__)

PING_TIMEOUT_S = 2.0
CONNECT_TIMEOUT_S = 10.0

# Statuses and message fragments with which servers report an image they can't decode
# (Ollama 0.35 answers 400 "Failed to load image or audio file", captured 2026-10-04;
# the other fragments cover other servers and versions).
UNREADABLE_IMAGE_STATUSES = frozenset({400, 415, 422, 500})
UNREADABLE_IMAGE_MESSAGES = (
    "image: unknown format",
    "illegal base64",
    "failed to decode image",
    "failed to load image",
    "invalid image",
    "unsupported image",
)
# Whole words only: `invalid image_url` is a request error, not an undecodable image.
_UNREADABLE_IMAGE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(m) for m in UNREADABLE_IMAGE_MESSAGES) + r")(?!\w)",
    re.IGNORECASE,
)
MAX_REASON_MESSAGE = 200
# Configuration errors: a bad LLM_BASE_URL. Retrying can't help, so they are never retried.
_CONFIG_ERRORS = (httpx.InvalidURL, httpx.UnsupportedProtocol)


@dataclass(frozen=True)
class ChatCompletion:
    """The parts of a chat completion the extractor needs."""

    content: str | None
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    cut_off: bool


class LLMClient:
    """Talks to `{base_url}` (an OpenAI-compatible `/v1` root) over httpx."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 1,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self._transport = transport

    def _client(self, timeout: float | httpx.Timeout) -> httpx.Client:
        return httpx.Client(
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=timeout,
            transport=self._transport,
        )

    def ping(self) -> bool:
        """Return True if `GET {base_url}/models` answers 2xx. Never raises."""
        try:
            with self._client(min(PING_TIMEOUT_S, self.timeout)) as client:
                response = client.get(f"{self.base_url}/models")
            return response.is_success
        except Exception as exc:  # health must never fail because of the model server
            logger.debug("LLM ping failed: %s", type(exc).__name__)
            return False

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int,
        response_format: dict[str, Any] | None = None,
    ) -> ChatCompletion:
        """POST `{base_url}/chat/completions` and return the first choice.

        Retries up to `max_retries` times on timeouts and transport errors only; an
        invalid base URL or protocol is `LLMError` at once.
        Raises `LLMUnavailable`, `LLMTimeout`, `UnreadableImage` or `LLMError`.
        """
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            body["response_format"] = response_format

        url = f"{self.base_url}/chat/completions"
        timeout = httpx.Timeout(self.timeout, connect=min(CONNECT_TIMEOUT_S, self.timeout))
        attempts = 1 + self.max_retries
        with self._client(timeout) as client:
            for attempt in range(1, attempts + 1):
                try:
                    response = client.post(url, json=body)
                    break
                # UnsupportedProtocol is a TransportError: this clause must come first.
                except _CONFIG_ERRORS as exc:
                    name = type(exc).__name__
                    logger.warning("LLM call failed: invalid model server URL (%s)", name)
                    # The exception text may quote the URL; the reason names only the type.
                    raise LLMError(f"invalid model server URL ({name})") from None
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    name = type(exc).__name__
                    logger.warning("LLM call attempt %d/%d failed: %s", attempt, attempts, name)
                    if attempt == attempts:
                        raise _transport_error(exc, attempts) from None
        return _parse_response(response, max_tokens, self.api_key)


def _transport_error(exc: Exception, attempts: int) -> LLMUnavailable | LLMTimeout:
    reason = f"{type(exc).__name__} after {attempts} attempt(s)"
    # A connect timeout means the server isn't reachable, not that the model is slow.
    if isinstance(exc, httpx.ConnectTimeout) or not isinstance(exc, httpx.TimeoutException):
        return LLMUnavailable(reason)
    return LLMTimeout(reason)


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(error, str):
            return error
    return response.text


def _http_error(response: httpx.Response, api_key: str) -> LLMError | UnreadableImage:
    message = _error_message(response)
    # A server may echo the Authorization header. Scrub before truncating, so a key that
    # straddles the cut can't leave a prefix behind.
    if api_key:
        message = message.replace(api_key, "[key]")
    reason = f"HTTP {response.status_code}: {message[:MAX_REASON_MESSAGE]}"
    if response.status_code in UNREADABLE_IMAGE_STATUSES and _UNREADABLE_IMAGE.search(message):
        return UnreadableImage(reason)
    return LLMError(reason)


def _parse_response(response: httpx.Response, max_tokens: int, api_key: str) -> ChatCompletion:
    if not response.is_success:
        error = _http_error(response, api_key)
        logger.warning("LLM call failed: HTTP %d", response.status_code)
        raise error
    try:
        payload = response.json()
        choice = payload["choices"][0]
        content = choice["message"]["content"]
        if content is not None and not isinstance(content, str):
            raise TypeError("content is not a string")
        finish_reason = choice.get("finish_reason")
        usage = payload.get("usage") or {}
        prompt_tokens = _int_or_none(usage.get("prompt_tokens"))
        completion_tokens = _int_or_none(usage.get("completion_tokens"))
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        reason = f"HTTP {response.status_code}: broken envelope ({type(exc).__name__})"
        raise LLMError(reason) from None
    cut_off = finish_reason == "length" or (
        completion_tokens is not None and completion_tokens >= max_tokens
    )
    return ChatCompletion(
        content=content,
        finish_reason=finish_reason if isinstance(finish_reason, str) else None,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cut_off=cut_off,
    )


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None

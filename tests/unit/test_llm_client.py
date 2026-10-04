"""`LLMClient`: `ping` never raises; `chat_completion` maps failures to typed errors."""

import contextlib
import json
import logging
from collections.abc import Callable

import httpx
import pytest

from app.ai.client import ChatCompletion, LLMClient
from app.errors import LLMError, LLMTimeout, LLMUnavailable, UnreadableImage

API_KEY = "sk-test-not-a-real-key"
SECRET_TEXT = "Kassenbon Inhalt geheim"
IMAGE_B64 = "aGVsbG8tYmFzZTY0LWltYWdlLWJ5dGVz"
MESSAGES = [
    {"role": "system", "content": SECRET_TEXT},
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "Transcribe."},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{IMAGE_B64}"}},
        ],
    },
]


def client_with(handler, *, max_retries: int = 1, api_key: str = "ollama") -> LLMClient:
    return LLMClient(
        base_url="http://model-server/v1/",
        api_key=api_key,
        model="gemma3:4b",
        timeout=120,
        transport=httpx.MockTransport(handler),
        max_retries=max_retries,
    )


def completion_body(
    content: str | None = '{"ok": true}',
    finish_reason: str | None = "stop",
    usage: dict | None = None,
) -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "gemma3:4b",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage
        if usage is not None
        else {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def recording(
    *outcomes: httpx.Response | type[Exception],
) -> tuple[Callable[[httpx.Request], httpx.Response], list[httpx.Request]]:
    """A handler that answers or raises per call; the last outcome repeats."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        outcome = outcomes[min(len(seen), len(outcomes)) - 1]
        if isinstance(outcome, httpx.Response):
            return outcome
        raise outcome("boom", request=request)

    return handler, seen


def call(client: LLMClient, max_tokens: int = 2048, **kwargs) -> ChatCompletion:
    return client.chat_completion(MESSAGES, temperature=0, max_tokens=max_tokens, **kwargs)


# ---------------------------------------------------------------- ping


def test_ping_ok_calls_models_with_bearer_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": []})

    assert client_with(handler).ping() is True
    assert str(seen[0].url) == "http://model-server/v1/models"
    assert seen[0].headers["Authorization"] == "Bearer ollama"


def test_ping_server_error_is_down() -> None:
    assert client_with(lambda request: httpx.Response(500)).ping() is False


def test_ping_connection_error_is_down() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert client_with(handler).ping() is False


# ---------------------------------------------------------------- request and parsing


def test_chat_completion_sends_the_openai_request() -> None:
    handler, seen = recording(httpx.Response(200, json=completion_body()))
    response_format = {"type": "json_schema", "json_schema": {"name": "x", "schema": {}}}

    call(client_with(handler, api_key=API_KEY), max_tokens=512, response_format=response_format)

    [request] = seen
    assert request.method == "POST"
    assert str(request.url) == "http://model-server/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert json.loads(request.content) == {
        "model": "gemma3:4b",
        "messages": MESSAGES,
        "temperature": 0,
        "max_tokens": 512,
        "response_format": response_format,
    }


def test_response_format_is_left_out_when_not_given() -> None:
    handler, seen = recording(httpx.Response(200, json=completion_body()))

    call(client_with(handler))

    assert "response_format" not in json.loads(seen[0].content)


def test_chat_completion_parses_the_first_choice() -> None:
    usage = {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33, "extra": 1}
    handler, _ = recording(httpx.Response(200, json=completion_body("hi", "stop", usage)))

    assert call(client_with(handler)) == ChatCompletion(
        content="hi",
        finish_reason="stop",
        prompt_tokens=11,
        completion_tokens=22,
        cut_off=False,
    )


def test_null_content_and_missing_usage_are_accepted() -> None:
    body = completion_body(content=None)
    del body["usage"]
    handler, _ = recording(httpx.Response(200, json=body))

    result = call(client_with(handler))

    assert result.content is None
    assert (result.prompt_tokens, result.completion_tokens) == (None, None)


@pytest.mark.parametrize(
    ("finish_reason", "completion_tokens", "max_tokens", "cut_off"),
    [
        ("stop", 20, 2048, False),
        ("length", 20, 2048, True),
        ("stop", 2048, 2048, True),
        ("stop", 2049, 2048, True),
        ("stop", 2047, 2048, False),
        (None, None, 2048, False),
        ("length", None, 2048, True),
    ],
)
def test_cut_off(
    finish_reason: str | None, completion_tokens: int | None, max_tokens: int, cut_off: bool
) -> None:
    usage = {"prompt_tokens": 1, "completion_tokens": completion_tokens}
    handler, _ = recording(httpx.Response(200, json=completion_body("x", finish_reason, usage)))

    assert call(client_with(handler), max_tokens=max_tokens).cut_off is cut_off


# ---------------------------------------------------------------- retries and error mapping


def test_timeout_then_success_retries_once() -> None:
    handler, seen = recording(httpx.ReadTimeout, httpx.Response(200, json=completion_body("ok")))

    assert call(client_with(handler)).content == "ok"
    assert len(seen) == 2


@pytest.mark.parametrize("max_retries", [0, 1, 2])
def test_timeout_every_time_raises_llm_timeout(max_retries: int) -> None:
    handler, seen = recording(httpx.ReadTimeout)

    with pytest.raises(LLMTimeout):
        call(client_with(handler, max_retries=max_retries))
    assert len(seen) == 1 + max_retries


@pytest.mark.parametrize(
    "exc", [httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError]
)
def test_connection_errors_raise_llm_unavailable(exc: type[Exception]) -> None:
    handler, seen = recording(exc)

    with pytest.raises(LLMUnavailable) as raised:
        call(client_with(handler))
    assert len(seen) == 2
    assert exc.__name__ in raised.value.reason


def test_connection_error_then_success() -> None:
    handler, seen = recording(httpx.ConnectError, httpx.Response(200, json=completion_body("ok")))

    assert call(client_with(handler)).content == "ok"
    assert len(seen) == 2


@pytest.mark.parametrize("status", [404, 500, 503])
def test_http_error_raises_llm_error_without_retry(status: int) -> None:
    body = {"error": {"message": 'model "x" not found', "type": "api_error"}}
    handler, seen = recording(httpx.Response(status, json=body))

    with pytest.raises(LLMError) as raised:
        call(client_with(handler))
    assert len(seen) == 1
    assert raised.value.reason == f'HTTP {status}: model "x" not found'


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(500, json={"error": "plain string"}), "HTTP 500: plain string"),
        (httpx.Response(502, text="Bad gateway"), "HTTP 502: Bad gateway"),
        (httpx.Response(500, json={"detail": "x"}), 'HTTP 500: {"detail":"x"}'),
        (httpx.Response(500, text="y" * 500), "HTTP 500: " + "y" * 200),
    ],
)
def test_error_message_sources(response: httpx.Response, reason: str) -> None:
    handler, _ = recording(response)

    with pytest.raises(LLMError) as raised:
        call(client_with(handler))
    assert raised.value.reason == reason


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"message": {}}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": 5}}]}),
        httpx.Response(200, json={"choices": [None]}),
    ],
)
def test_broken_envelope_raises_llm_error(response: httpx.Response) -> None:
    handler, seen = recording(response)

    with pytest.raises(LLMError):
        call(client_with(handler))
    assert len(seen) == 1


@pytest.mark.parametrize(
    ("status", "message", "expected"),
    [
        (500, "image: unknown format", UnreadableImage),
        (400, "Illegal base64 data at input byte 4", UnreadableImage),
        (422, "Failed to decode image", UnreadableImage),
        (415, "unsupported image type", UnreadableImage),
        (400, "failed to load image from bytes", UnreadableImage),
        (500, "Invalid image data", UnreadableImage),
        (404, "image: unknown format", LLMError),
        (503, "invalid image", LLMError),
        (500, "this model is missing data required for image input", LLMError),
        (400, "bad request", LLMError),
        (400, "invalid image_url: expected a data URL", LLMError),
        (400, "unsupported image_url scheme", LLMError),
        (500, "decode: invalid image.", UnreadableImage),
    ],
)
def test_unreadable_image(status: int, message: str, expected: type[Exception]) -> None:
    handler, seen = recording(httpx.Response(status, json={"error": {"message": message}}))

    with pytest.raises(expected) as raised:
        call(client_with(handler))
    assert type(raised.value) is expected
    assert len(seen) == 1


def test_api_key_never_appears_in_a_reason() -> None:
    echo = {"error": {"message": f"invalid key Bearer {API_KEY}"}}
    handler, _ = recording(httpx.Response(401, json=echo))

    with pytest.raises(LLMError) as raised:
        call(client_with(handler, api_key=API_KEY))
    assert API_KEY not in raised.value.reason
    assert API_KEY not in str(raised.value)


@pytest.mark.parametrize("before", [180, 190, 195, 199, 200])
@pytest.mark.parametrize("error", [LLMError, UnreadableImage])
def test_api_key_straddling_the_cut_leaves_no_prefix(before: int, error: type) -> None:
    # Scrubbing after truncation would leave the key's first characters in the reason.
    tail = " image: unknown format" if error is UnreadableImage else ""
    echo = {"error": {"message": "x" * before + API_KEY + tail}}
    handler, _ = recording(httpx.Response(500, json=echo))

    with pytest.raises(error) as raised:
        call(client_with(handler, api_key=API_KEY))
    reason = raised.value.reason
    assert type(raised.value) is error
    for size in range(4, len(API_KEY) + 1):
        assert API_KEY[:size] not in reason
    assert reason.startswith("HTTP 500: " + "x" * min(before, 200))


@pytest.mark.parametrize(
    "exc", [httpx.UnsupportedProtocol, httpx.InvalidURL], ids=["protocol", "url"]
)
def test_config_errors_are_llm_error_without_retry(exc: type[Exception]) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if exc is httpx.InvalidURL:
            raise httpx.InvalidURL(f"bad url with {API_KEY}")
        raise exc(f"bad scheme with {API_KEY}", request=request)

    with pytest.raises(LLMError) as raised:
        call(client_with(handler, api_key=API_KEY, max_retries=3))
    assert len(seen) == 1
    assert raised.value.reason == f"invalid model server URL ({exc.__name__})"
    assert API_KEY not in str(raised.value)


@pytest.mark.parametrize(
    ("base_url", "name"),
    [("ftp://model-server/v1", "UnsupportedProtocol"), ("http://[::1/v1", "InvalidURL")],
)
def test_bad_base_url_without_a_mock_is_llm_error(base_url: str, name: str) -> None:
    # No transport is given, so httpx itself rejects the URL; nothing leaves the process.
    client = LLMClient(base_url=base_url, api_key=API_KEY, model="m", timeout=5, max_retries=2)

    with pytest.raises(LLMError) as raised:
        call(client)
    assert raised.value.reason == f"invalid model server URL ({name})"
    assert API_KEY not in str(raised.value)


def test_undecodable_body_is_llm_error_without_retry() -> None:
    # A 200 that claims gzip but isn't: httpx raises DecodingError while reading the body.
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=b"not gzip")

    with pytest.raises(LLMError) as raised:
        call(client_with(handler, max_retries=3))
    assert len(seen) == 1
    assert raised.value.reason == "request failed (DecodingError)"


def test_redirect_loop_is_llm_error_without_retry() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        raise httpx.TooManyRedirects(f"loop with {API_KEY}", request=request)

    with pytest.raises(LLMError) as raised:
        call(client_with(handler, api_key=API_KEY, max_retries=3))
    assert len(seen) == 1
    assert raised.value.reason == "request failed (TooManyRedirects)"
    assert API_KEY not in str(raised.value)


@pytest.mark.parametrize("exc", [httpx.ReadTimeout, httpx.ConnectError])
def test_api_key_not_in_transport_error_reason(exc: type[Exception]) -> None:
    handler, _ = recording(exc)

    with pytest.raises((LLMTimeout, LLMUnavailable)) as raised:
        call(client_with(handler, api_key=API_KEY))
    assert API_KEY not in raised.value.reason


@pytest.mark.parametrize(
    "outcomes",
    [
        (httpx.ReadTimeout, httpx.Response(200, json=completion_body(SECRET_TEXT))),
        (httpx.ConnectError,),
        (httpx.Response(500, json={"error": {"message": "image: unknown format"}}),),
        (httpx.Response(200, text="broken"),),
    ],
)
def test_logs_contain_no_message_content(caplog: pytest.LogCaptureFixture, outcomes) -> None:
    handler, _ = recording(*outcomes)
    caplog.set_level(logging.DEBUG)

    with contextlib.suppress(LLMError, LLMTimeout, LLMUnavailable, UnreadableImage):
        call(client_with(handler, api_key=API_KEY))

    logged = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (SECRET_TEXT, IMAGE_B64, "Transcribe", API_KEY):
        assert secret not in logged

"""`LLMClient.ping` reports reachability and never raises."""

import httpx

from app.ai.client import LLMClient


def client_with(handler) -> LLMClient:
    return LLMClient(
        base_url="http://model-server/v1/",
        api_key="ollama",
        model="gemma3:4b",
        timeout=120,
        transport=httpx.MockTransport(handler),
    )


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

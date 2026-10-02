"""Minimal client for an OpenAI-compatible model server. F03 adds chat completions."""

import logging

import httpx

logger = logging.getLogger(__name__)

PING_TIMEOUT_S = 2.0


class LLMClient:
    """Talks to `{base_url}` (an OpenAI-compatible `/v1` root) over httpx."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._transport = transport

    def _client(self, timeout: float) -> httpx.Client:
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

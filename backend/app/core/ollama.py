"""Minimal async client for the local Ollama server.

Only local hosts are allowed, so no document data can leave this machine.
"""

from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class OllamaError(RuntimeError):
    """Raised when Ollama is unreachable or returns an error."""


class OllamaClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.ollama_url).rstrip("/")
        self.model = model or settings.ollama_model
        host = urlparse(self.base_url).hostname
        if host not in LOCAL_HOSTS:
            raise ValueError(
                f"Ollama URL must point to this machine, got host {host!r}"
            )
        self._timeout = timeout
        self._transport = transport

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout or self._timeout,
            transport=self._transport,
        )

    async def list_models(self, timeout: float | None = None) -> list[str]:
        try:
            async with self._client(timeout) as client:
                resp = await client.get("/api/tags")
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaError(f"Could not list Ollama models: {exc}") from exc
        return [m["name"] for m in resp.json().get("models", [])]

    async def is_available(self, timeout: float | None = 3.0) -> bool:
        """True if Ollama is reachable and the configured model is installed."""
        try:
            return self.model in await self.list_models(timeout)
        except OllamaError:
            return False

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        options: dict[str, Any] | None = None,
        format: str | dict[str, Any] | None = None,
    ) -> str:
        """Send a non-streaming chat request and return the assistant's text."""
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
        }
        if options:
            payload["options"] = options
        if format is not None:
            payload["format"] = format
        try:
            async with self._client() as client:
                resp = await client.post("/api/chat", json=payload)
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise OllamaError(
                f"Ollama returned {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Could not reach Ollama: {exc}") from exc
        return resp.json().get("message", {}).get("content", "")

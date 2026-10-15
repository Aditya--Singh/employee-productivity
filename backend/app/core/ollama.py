"""Minimal async client for the local Ollama server.

Only local hosts are allowed, so no document data can leave this machine.

Every chat request carries an explicit ``num_ctx``. Ollama otherwise uses a small default
context window and silently drops the start of any prompt that does not fit. A size guard
rejects prompts that would not fit, instead of letting Ollama truncate them.
"""

import json
import math
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

# Deliberately high estimate: real English text is about 4 characters per token.
CHARS_PER_TOKEN = 3
TOKENS_PER_MESSAGE = 8
DEFAULT_EXPECTED_OUTPUT_TOKENS = 1024


class OllamaError(RuntimeError):
    """Raised when Ollama is unreachable or returns an error."""


class OllamaContextError(OllamaError):
    """Raised before sending when a prompt would not fit in the context window."""

    def __init__(self, estimated_tokens: int, limit: int) -> None:
        self.estimated_tokens = estimated_tokens
        self.limit = limit
        super().__init__(
            f"Prompt needs about {estimated_tokens} tokens including the expected "
            f"output, but the context window is {limit} tokens."
        )


def estimate_tokens(messages: list[dict[str, str]]) -> int:
    """Over-estimate the number of prompt tokens for a list of chat messages."""
    return sum(
        math.ceil(len(m.get("content", "")) / CHARS_PER_TOKEN) + TOKENS_PER_MESSAGE
        for m in messages
    )


class OllamaClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        num_ctx: int | None = None,
        timeout: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.ollama_url).rstrip("/")
        self.model = model or settings.ollama_model
        self.num_ctx = num_ctx or settings.ollama_num_ctx
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

    def _build_payload(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None,
        options: dict[str, Any] | None,
        format: str | dict[str, Any] | None,
        stream: bool,
        expected_output_tokens: int,
    ) -> dict[str, Any]:
        needed = estimate_tokens(messages) + expected_output_tokens
        if needed > self.num_ctx:
            raise OllamaContextError(needed, self.num_ctx)
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": stream,
            # The client's num_ctx always wins, so callers cannot drop or change it.
            "options": {**(options or {}), "num_ctx": self.num_ctx},
        }
        if format is not None:
            payload["format"] = format
        return payload

    async def chat_response(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        options: dict[str, Any] | None = None,
        format: str | dict[str, Any] | None = None,
        expected_output_tokens: int = DEFAULT_EXPECTED_OUTPUT_TOKENS,
    ) -> dict[str, Any]:
        """Send a non-streaming chat request and return Ollama's full JSON response."""
        payload = self._build_payload(
            messages,
            model=model,
            options=options,
            format=format,
            stream=False,
            expected_output_tokens=expected_output_tokens,
        )
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
        return resp.json()

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        options: dict[str, Any] | None = None,
        format: str | dict[str, Any] | None = None,
        expected_output_tokens: int = DEFAULT_EXPECTED_OUTPUT_TOKENS,
    ) -> str:
        """Send a non-streaming chat request and return the assistant's text."""
        data = await self.chat_response(
            messages,
            model=model,
            options=options,
            format=format,
            expected_output_tokens=expected_output_tokens,
        )
        return data.get("message", {}).get("content", "")

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        options: dict[str, Any] | None = None,
        format: str | dict[str, Any] | None = None,
        expected_output_tokens: int = DEFAULT_EXPECTED_OUTPUT_TOKENS,
    ) -> AsyncIterator[str]:
        """Stream the assistant's text in chunks as Ollama generates it.

        Closing the iterator early closes the HTTP stream, which stops generation.
        """
        payload = self._build_payload(
            messages,
            model=model,
            options=options,
            format=format,
            stream=True,
            expected_output_tokens=expected_output_tokens,
        )
        try:
            async with (
                self._client() as client,
                client.stream("POST", "/api/chat", json=payload) as resp,
            ):
                if resp.is_error:
                    body = (await resp.aread()).decode(errors="replace")
                    raise OllamaError(f"Ollama returned {resp.status_code}: {body}")
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise OllamaError(
                            f"Ollama sent an invalid stream line: {line[:200]}"
                        ) from exc
                    if "error" in data:
                        raise OllamaError(f"Ollama error: {data['error']}")
                    chunk = data.get("message", {}).get("content", "")
                    if chunk:
                        yield chunk
                    if data.get("done"):
                        return
        except httpx.HTTPError as exc:
            raise OllamaError(f"Could not reach Ollama: {exc}") from exc

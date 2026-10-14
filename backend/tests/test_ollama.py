import asyncio
import json

import httpx
import pytest

from app.core.ollama import OllamaClient, OllamaError


def run(coro):
    return asyncio.run(coro)


def make_client(handler, **kw):
    return OllamaClient(
        base_url="http://localhost:11434",
        model="gpt-oss:20b",
        transport=httpx.MockTransport(handler),
        **kw,
    )


def test_list_models_and_is_available():
    def handler(req):
        assert req.url.path == "/api/tags"
        return httpx.Response(
            200, json={"models": [{"name": "gpt-oss:20b"}, {"name": "x"}]}
        )

    c = make_client(handler)
    assert run(c.list_models()) == ["gpt-oss:20b", "x"]
    assert run(c.is_available()) is True


def test_is_available_false_when_down():
    def handler(req):
        raise httpx.ConnectError("refused")

    assert run(make_client(handler).is_available()) is False


def test_chat_sends_payload_and_returns_content():
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return httpx.Response(
            200, json={"message": {"role": "assistant", "content": "Hi"}}
        )

    out = run(
        make_client(handler).chat(
            [{"role": "user", "content": "Hello"}],
            options={"temperature": 0},
            format="json",
        )
    )
    assert out == "Hi"
    assert seen == {
        "model": "gpt-oss:20b",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": False,
        "options": {"temperature": 0},
        "format": "json",
    }


def test_chat_http_error_raises():
    c = make_client(lambda req: httpx.Response(404, text="model not found"))
    with pytest.raises(OllamaError, match="404"):
        run(c.chat([{"role": "user", "content": "x"}]))


def test_chat_connection_error_raises():
    def handler(req):
        raise httpx.ConnectError("refused")

    with pytest.raises(OllamaError, match="Could not reach"):
        run(make_client(handler).chat([{"role": "user", "content": "x"}]))


@pytest.mark.parametrize("url", ["https://api.example.com", "http://10.0.0.5:11434"])
def test_non_local_url_rejected(url):
    with pytest.raises(ValueError, match="this machine"):
        OllamaClient(base_url=url)


def test_model_from_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "gpt-oss:20b")
    assert OllamaClient().model == "gpt-oss:20b"


def test_default_model_is_gpt_oss_20b(monkeypatch):
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    assert OllamaClient().model == "gpt-oss:20b"

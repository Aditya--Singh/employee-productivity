"""A fake Ollama server for tests, built on httpx.MockTransport."""

import json

import httpx

from app.core.ollama import OllamaClient


class FakeOllama:
    def __init__(self, reply: str = "Summary text.", chunk_replies=None, down=False):
        self.reply = reply
        self.chunk_replies = chunk_replies
        self.down = down
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused")
        body = json.loads(request.content)
        self.requests.append(body)
        if body["stream"]:
            words = self.reply.split(" ")
            lines = [
                json.dumps({"message": {"content": w + (" " if i < len(words) - 1 else "")}})
                for i, w in enumerate(words)
            ]
            lines.append(json.dumps({"done": True}))
            return httpx.Response(200, content="\n".join(lines).encode())
        n = sum(1 for r in self.requests if not r["stream"])
        content = f"- partial {n}" if self.chunk_replies is None else self.chunk_replies
        return httpx.Response(200, json={"message": {"content": content}})

    def client(self, num_ctx: int | None = None) -> OllamaClient:
        return OllamaClient(
            base_url="http://localhost:11434",
            model="gpt-oss:20b",
            num_ctx=num_ctx or 32768,
            transport=httpx.MockTransport(self.handler),
        )

"""Real-Ollama context-window test (spec section 3.8).

Opt-in: run with `uv run pytest -m ollama`. Needs Ollama running with the configured model.
"""

import asyncio
import secrets

import pytest

from app.core.ollama import OllamaClient
from tests.docgen import make_document

pytestmark = pytest.mark.ollama


def test_twenty_page_document_is_not_truncated():
    # Ollama truncates from the START of an over-long prompt, so the first-line code is the
    # real proof. It is also random per run, which defeats Ollama's prompt-prefix cache
    # (a cached prefix would lower prompt_eval_count on reruns).
    first_code = f"REF-{secrets.token_hex(3).upper()}"
    last_code = f"PNB-{secrets.token_hex(3).upper()}"
    doc = make_document(
        pages=20,
        first_line=f"The reference code is {first_code}.",
        last_line=f"The verification code is {last_code}.",
    )
    messages = [
        {
            "role": "system",
            "content": "You read documents carefully and answer with exactly what is asked.",
        },
        {
            "role": "user",
            "content": doc
            + "\n\nWhat is the reference code in the first line of the document above, "
            "and what is the verification code in its last line? "
            "Reply with the two codes only, separated by a space.",
        },
    ]
    client = OllamaClient(timeout=900.0)
    data = asyncio.run(client.chat_response(messages, options={"temperature": 0}))
    reply = data["message"]["content"]
    # Every word is at least one token, so the word count is a safe lower bound.
    words = len(doc.split())
    print(
        f"\nchars={len(doc)} words={words} "
        f"prompt_eval_count={data.get('prompt_eval_count')} reply={reply!r}"
    )
    assert first_code in reply
    assert last_code in reply
    assert data["prompt_eval_count"] >= words

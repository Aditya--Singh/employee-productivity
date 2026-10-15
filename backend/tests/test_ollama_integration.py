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


def test_rewrite_keeps_paragraphs_and_numbers():
    """Phase 3: the real model rewrites a section without merging paragraphs or adding
    numbers. The rewrite's own validation retries once; the result must pass it."""
    from app.tabs.document_assistant import segments
    from app.tabs.document_assistant.rewrite import (
        new_numbers,
        rewrite_document,
    )

    source = (
        "we got ur loan application for Rs 5,00,000 on 12 March 2026.\n\n"
        "pls send the KYC docs to the branch within 15 days or the file will be closed.\n\n"
        "call the branch on 0294-2410000 if u have questions."
    )
    doc = segments.from_text(source)

    async def collect():
        client = OllamaClient(timeout=900.0)
        return [e async for e in rewrite_document(doc, "formal", client)]

    events = asyncio.run(collect())
    ends = [e for e in events if e["type"] == "section_end"]
    notices = [e["text"] for e in events if e["type"] == "notice"]
    out = [t for e in ends for t in e["texts"]]
    print(f"\nrewrite={out!r} notices={notices!r}")
    assert not notices, "the model's rewrite failed validation twice"
    assert len(out) == 3
    assert out != [s.text for s in doc.segments]
    assert new_numbers(source, "\n\n".join(out)) == set()


def test_proofread_letter_flags_planted_errors_and_no_bank_terms():
    """Regression test on samples/test_proofread_letter.docx with real Ollama and real
    LanguageTool: all nine planted errors are flagged and no bank term is touched."""
    from app.core.languagetool import LanguageToolClient
    from app.tabs.document_assistant import segments
    from app.tabs.document_assistant.bank_terms import BankTerms
    from app.tabs.document_assistant.proofread import proofread_document
    from tests.document_assistant.test_proofread import LETTER, flagged, planted_spans

    lt = LanguageToolClient()
    if not asyncio.run(lt.is_available()):
        pytest.fail("LanguageTool is not running at the configured URL")
    doc = segments.from_docx(LETTER.read_bytes())

    async def collect():
        client = OllamaClient(timeout=900.0)
        return [e async for e in proofread_document(doc, client, lt)]

    result = asyncio.run(collect())[-1]
    assert result["type"] == "result", result
    text, suggestions = result["text"], result["suggestions"]
    print(
        "\n"
        + "\n".join(
            f"{s['source']:12} {s['category']:11} {text[s['start'] : s['end']]!r} -> "
            f"{s['replacements'][:2]}"
            for s in suggestions
        )
    )
    assert result["notices"] == []
    missed = [
        error
        for error, (start, end) in planted_spans(text).items()
        if not flagged(text, suggestions, start, end)
    ]
    assert missed == []
    touched = [
        term
        for start, end, term in BankTerms.load().spans(text)
        if flagged(text, suggestions, start, end)
    ]
    assert touched == []

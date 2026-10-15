import asyncio
import json

import pytest

from app.tabs.document_assistant import segments
from app.tabs.document_assistant.proofread import (
    ISSUES_SCHEMA,
    LLM_DOWN_NOTICE,
    LT_DOWN_NOTICE,
    ProofreadUnavailable,
    Suggestion,
    apply_to_text,
    merge,
    parse_issues,
    proofread_document,
)
from app.tabs.document_assistant.rewrite import DocumentTooLong
from tests.docgen import make_document

from .fixtures import make_rich_docx
from .mock_ollama import FakeLanguageTool, FakeOllama


def issues(*items) -> str:
    return json.dumps(
        {
            "issues": [
                {"original": o, "replacement": r, "explanation": e} for o, r, e in items
            ]
        }
    )


def llm(reply: str) -> FakeOllama:
    """A fake model that always gives `reply` to non-streaming requests."""
    return FakeOllama(responder=lambda body: reply)


def run(doc, fake=None, lt=None):
    fake = fake or llm(issues())
    lt = lt or FakeLanguageTool()

    async def collect():
        return [e async for e in proofread_document(doc, fake.client(), lt.client())]

    events = asyncio.run(collect())
    return events, events[-1], fake


def sug(start, end, source="llm"):
    return Suggestion(start, end, "x", "m", ["r"], source)


def test_merge_prefers_languagetool_and_drops_overlaps():
    lt = [sug(0, 5, "languagetool"), sug(20, 25, "languagetool")]
    llm = [sug(3, 10), sug(10, 15), sug(12, 18), sug(24, 30), sug(40, 41)]
    merged = merge(lt, llm)
    assert [(s.start, s.end, s.source) for s in merged] == [
        (0, 5, "languagetool"),
        (10, 15, "llm"),
        (20, 25, "languagetool"),
        (40, 41, "llm"),
    ]
    assert [s.id for s in merged] == ["s1", "s2", "s3", "s4"]


def test_merge_treats_insertions_as_one_character():
    merged = merge([sug(5, 5, "languagetool")], [sug(5, 8)])
    assert len(merged) == 1


def test_combines_both_sources_with_offsets():
    text = "Teh branch will do the needful at the earliest.\n\nAll is well."
    fake = llm(
        issues(("do the needful", "take the necessary action", "Unclear idiom."))
    )
    lt = FakeLanguageTool({"Teh": "The"})
    _, result, fake = run(segments.from_text(text), fake, lt)
    assert result["type"] == "result"
    assert result["text"] == text
    found = [
        (text[s["start"] : s["end"]], s["category"], s["source"])
        for s in result["suggestions"]
    ]
    assert found == [
        ("Teh", "spelling", "languagetool"),
        ("do the needful", "clarity", "llm"),
    ]
    assert result["suggestions"][1]["replacements"] == ["take the necessary action"]
    assert all(s["applicable"] for s in result["suggestions"])
    assert result["notices"] == []
    body = fake.requests[0]
    assert body["format"] == ISSUES_SCHEMA
    assert body["options"]["num_ctx"] == 32768
    assert lt.requests[0]["language"] == "en-GB"


def test_llm_issues_not_found_or_adding_numbers_are_dropped():
    text = "Please pay the amount soon."
    fake = llm(
        issues(
            ("not in the text", "x", "Hallucinated."),
            ("the amount", "Rs 500", "Adds a number."),
            ("soon", "soon", "No change."),
            ("pay", "remit", "Formal."),
        )
    )
    _, result, _ = run(segments.from_text(text), fake)
    assert [(s["start"], s["end"]) for s in result["suggestions"]] == [(7, 10)]


def test_invalid_json_is_retried_once():
    replies = iter(["not json at all", issues(("pay", "remit", "Formal."))])
    fake = FakeOllama(responder=lambda body: next(replies))
    _, result, fake = run(segments.from_text("Please pay."), fake)
    assert len(fake.requests) == 2
    assert len(result["suggestions"]) == 1


def test_invalid_json_twice_gives_notice_and_keeps_languagetool():
    fake = llm("still not json")
    lt = FakeLanguageTool({"Teh": "The"})
    _, result, fake = run(segments.from_text("Teh text."), fake, lt)
    assert len(fake.requests) == 2
    assert len(result["suggestions"]) == 1
    assert any(
        "Clarity could not be checked for part(s) 1" in n for n in result["notices"]
    )


def test_parse_issues_accepts_wrapped_json_and_skips_bad_items():
    reply = 'Here you go: {"issues": [{"original": "a", "replacement": "b"}, 5]}'
    assert parse_issues(reply) == [
        {"original": "a", "replacement": "b", "explanation": "Clearer wording."}
    ]
    with pytest.raises(ValueError):
        parse_issues('{"problems": []}')


def test_languagetool_down_returns_llm_with_notice():
    fake = llm(issues(("pay", "remit", "Formal.")))
    _, result, _ = run(
        segments.from_text("Please pay."), fake, FakeLanguageTool(down=True)
    )
    assert [s["source"] for s in result["suggestions"]] == ["llm"]
    assert result["notices"] == [LT_DOWN_NOTICE]


def test_llm_down_returns_languagetool_with_notice():
    lt = FakeLanguageTool({"Teh": "The"})
    _, result, _ = run(segments.from_text("Teh text."), FakeOllama(down=True), lt)
    assert [s["source"] for s in result["suggestions"]] == ["languagetool"]
    assert result["notices"] == [LLM_DOWN_NOTICE]


def test_both_down_raises():
    with pytest.raises(ProofreadUnavailable):
        run(
            segments.from_text("Some text."),
            FakeOllama(down=True),
            FakeLanguageTool(down=True),
        )


def test_long_document_is_checked_in_sections():
    doc = segments.from_text(make_document(pages=20))
    events, _, fake = run(doc)
    assert len(fake.requests) > 10
    statuses = [e["text"] for e in events if e["type"] == "status"]
    assert statuses[0] == "Checking spelling and grammar…"
    assert statuses[-1].startswith(f"Checking clarity, part {len(fake.requests)} of")


def test_over_length_document_rejected():
    with pytest.raises(DocumentTooLong, match="Proofread"):
        run(segments.from_text("word " * 11_001))


def test_docx_suggestions_in_links_are_not_applicable():
    doc = segments.from_docx(make_rich_docx())
    lt = FakeLanguageTool({"websit": "website", "manger": "manager"})
    _, result, _ = run(doc, lt=lt)
    by_text = {
        doc.text[s["start"] : s["end"]]: s["applicable"] for s in result["suggestions"]
    }
    assert by_text == {"manger": True, "websit": False}
    assert any("cannot be applied" in n for n in result["notices"])


def test_apply_to_text():
    text = "Teh branch is closd."
    assert (
        apply_to_text(text, [(14, 19, "closed"), (0, 3, "The")])
        == "The branch is closed."
    )
    assert apply_to_text("ab", [(1, 1, "X")]) == "aXb"
    with pytest.raises(ValueError):
        apply_to_text(text, [(0, 5, "a"), (3, 8, "b")])
    with pytest.raises(ValueError):
        apply_to_text(text, [(0, 99, "a")])

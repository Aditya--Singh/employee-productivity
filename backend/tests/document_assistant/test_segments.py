from app.tabs.document_assistant import segments

from .fixtures import make_docx, make_rich_docx


def test_text_paragraphs_keep_offsets_into_original():
    text = "  First para\nsame para.\n\n\n   Second para.  \n \nThird."
    doc = segments.from_text(text)
    assert [s.text for s in doc.segments] == [
        "First para\nsame para.",
        "Second para.",
        "Third.",
    ]
    for s in doc.segments:
        assert text[s.start : s.end] == s.text
    assert doc.text == text


def test_text_segments_are_all_rewritable():
    doc = segments.from_text("One.\n\nTwo.")
    assert all(s.rewritable for s in doc.segments)


def test_locate_maps_offsets_and_rejects_cross_paragraph_spans():
    doc = segments.from_text("Alpha beta.\n\nGamma delta.")
    start = doc.text.index("delta")
    assert doc.locate(start, start + 5) == (1, 6, 11)
    assert doc.locate(5, start) is None


def test_docx_segments_cover_body_and_table_paragraphs():
    doc = segments.from_docx(
        make_docx(["First.", "", "Second."], table=[["A", "B"], ["C", "D"]])
    )
    assert [s.text for s in doc.segments] == ["First.", "Second.", "A", "B", "C", "D"]
    assert [s.in_table for s in doc.segments] == [False, False, True, True, True, True]
    assert doc.text == "First.\n\nSecond.\n\nA\n\nB\n\nC\n\nD"
    for s in doc.segments:
        assert doc.text[s.start : s.end] == s.text


def test_docx_hyperlink_text_is_locked():
    doc = segments.from_docx(make_rich_docx())
    link_para = doc.segments[3]
    assert link_para.text == "Visit our websit today."
    assert link_para.locked == ((6, 16),)
    assert not link_para.rewritable
    assert link_para.skip_reason == "contains a link, field or image"
    start = doc.text.index("websit")
    assert not doc.is_applicable(start, start + 6)
    today = doc.text.index("today")
    assert doc.is_applicable(today, today + 5)


def test_table_cells_are_not_rewritable_but_can_be_proofread():
    doc = segments.from_docx(make_rich_docx())
    cell = doc.segments[4]
    assert cell.in_table and not cell.rewritable
    assert doc.is_applicable(cell.start, cell.end)

"""Split a document into paragraphs ("segments") that Rewrite and Proofread work on.

Each segment knows where it starts in the document text, so an offset in the document maps
to one paragraph and an offset inside it. For DOCX files a segment index is also the
paragraph index used by `docx_writer`, which is what lets edits go back into the file.
"""

import io
import re
from dataclasses import dataclass, field

from .docx_writer import docx_paragraphs, locked_spans

PARAGRAPH_SEPARATOR = "\n\n"
_BREAK = re.compile(r"\n[ \t\r\f\v]*\n\s*")


@dataclass(frozen=True)
class Segment:
    start: int
    text: str
    in_table: bool = False
    # Ranges of `text` that cannot be edited in the file (links, fields, images).
    locked: tuple[tuple[int, int], ...] = ()

    @property
    def end(self) -> int:
        return self.start + len(self.text)

    @property
    def rewritable(self) -> bool:
        """Rewrite replaces whole paragraphs, so it skips table cells and locked text."""
        return not self.in_table and not self.locked

    @property
    def skip_reason(self) -> str:
        if self.in_table:
            return "table cell"
        if self.locked:
            return "contains a link, field or image"
        return ""


@dataclass
class SegmentedText:
    text: str
    segments: list[Segment] = field(default_factory=list)

    def locate(self, start: int, end: int) -> tuple[int, int, int] | None:
        """(segment index, local start, local end), or None if the span crosses paragraphs."""
        for i, seg in enumerate(self.segments):
            if seg.start <= start and end <= seg.end:
                return i, start - seg.start, end - seg.start
        return None

    def is_applicable(self, start: int, end: int) -> bool:
        located = self.locate(start, end)
        if located is None:
            return False
        i, s, e = located
        return not any(
            s < le and ls < max(e, s + 1) for ls, le in self.segments[i].locked
        )


def from_text(text: str) -> SegmentedText:
    """Paragraphs are separated by blank lines. Offsets point into `text` unchanged."""
    segments = []
    pos = 0
    for m in [*_BREAK.finditer(text), None]:
        end = m.start() if m else len(text)
        chunk = text[pos:end]
        stripped = chunk.strip()
        if stripped:
            segments.append(Segment(pos + chunk.index(stripped), stripped))
        if m:
            pos = m.end()
    return SegmentedText(text, segments)


def from_docx(content: bytes) -> SegmentedText:
    """One segment per non-empty body or table-cell paragraph, joined by blank lines."""
    from docx import Document

    paragraphs = docx_paragraphs(Document(io.BytesIO(content)))
    segments = []
    pos = 0
    for p in paragraphs:
        text = p.paragraph.text
        segments.append(
            Segment(pos, text, p.in_table, tuple(locked_spans(p.paragraph)))
        )
        pos += len(text) + len(PARAGRAPH_SEPARATOR)
    return SegmentedText(PARAGRAPH_SEPARATOR.join(s.text for s in segments), segments)

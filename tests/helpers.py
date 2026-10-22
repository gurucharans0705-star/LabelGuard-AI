"""Small builders so tests can create labels from plain text without any PDF."""
from __future__ import annotations

from labelguard.extraction.pdf_text import ExtractedDocument, PageContent, TextLine
from labelguard.fields.extractor import extract_fields
from labelguard.fields.models import LabelFields


def make_lines(texts: list[str], page: int = 1, sizes: dict[int, float] | None = None) -> list[TextLine]:
    """Stack text lines down a page. `sizes` maps a line index to a font size (default 10)."""
    sizes = sizes or {}
    return [
        TextLine(page, (10.0, 12.0 * index, 300.0, 12.0 * index + 10.0), text, sizes.get(index, 10.0))
        for index, text in enumerate(texts)
    ]


def document_from_lines(texts: list[str], sizes: dict[int, float] | None = None) -> ExtractedDocument:
    """A one-page ExtractedDocument whose lines are exactly `texts`."""
    lines = tuple(make_lines(texts, sizes=sizes))
    page = PageContent(
        page_number=1,
        text="\n".join(texts),
        blocks=(),
        width=320.0,
        height=400.0,
        image_count=0,
        lines=lines,
    )
    return ExtractedDocument(filename="synthetic.pdf", sha256="0" * 64, size_bytes=0, pages=(page,))


def fields_from_lines(texts: list[str], sizes: dict[int, float] | None = None) -> LabelFields:
    return extract_fields(document_from_lines(texts, sizes))

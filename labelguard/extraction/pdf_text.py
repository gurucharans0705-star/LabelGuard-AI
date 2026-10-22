"""Extract text, positioned lines and basic metadata from a PDF.

This module reads *digital* (selectable) text only. Pages with little or no
selectable text are detected here and handed to the OCR module by the ingestion
layer. Every line keeps its page number and bounding box so later findings can
point back to the exact place on the page as evidence.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from labelguard import config
from labelguard.extraction.normalize import normalize_line, normalize_text


class ExtractionError(Exception):
    """Raised when a file cannot be read as a usable PDF. Messages are user-friendly."""


@dataclass(frozen=True)
class TextBlock:
    page: int  # 1-based
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1 in PDF points
    text: str


@dataclass(frozen=True)
class TextLine:
    """One line of text with its position and (estimated) font size."""

    page: int  # 1-based
    bbox: tuple[float, float, float, float]
    text: str
    font_size: float | None = None


@dataclass(frozen=True)
class PageContent:
    page_number: int  # 1-based
    text: str
    blocks: tuple[TextBlock, ...]
    width: float
    height: float
    image_count: int
    lines: tuple[TextLine, ...] = ()
    source: str = "digital"  # "digital" (selectable text) or "ocr"
    ocr_confidence: float | None = None  # mean word confidence 0-100, OCR pages only

    @property
    def char_count(self) -> int:
        return len(self.text.strip())

    @property
    def has_little_text(self) -> bool:
        return self.char_count < config.OCR_MIN_CHARS

    @property
    def needs_ocr(self) -> bool:
        """Little text but images present: most likely a scan."""
        return self.has_little_text and self.image_count > 0

    @property
    def is_blank(self) -> bool:
        return self.has_little_text and self.image_count == 0


@dataclass(frozen=True)
class ExtractedDocument:
    filename: str
    sha256: str
    size_bytes: int
    metadata: dict[str, str] = field(default_factory=dict)
    pages: tuple[PageContent, ...] = ()

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def full_text(self) -> str:
        return "\n\n".join(page.text for page in self.pages)

    @property
    def char_count(self) -> int:
        return sum(page.char_count for page in self.pages)

    @property
    def pages_needing_ocr(self) -> list[int]:
        return [page.page_number for page in self.pages if page.needs_ocr]

    @property
    def ocr_pages(self) -> list[int]:
        return [page.page_number for page in self.pages if page.source == "ocr"]

    @property
    def unreadable_pages(self) -> list[int]:
        """Pages that still have almost no text and contain images (OCR missing or failed)."""
        return self.pages_needing_ocr

    @property
    def blocks(self) -> list[TextBlock]:
        return [block for page in self.pages for block in page.blocks]

    @property
    def all_lines(self) -> list[TextLine]:
        return [line for page in self.pages for line in page.lines]


def validate_pdf_bytes(data: bytes) -> None:
    """Cheap checks that need no PDF parsing: empty, too large, wrong format."""
    if not data:
        raise ExtractionError("The file is empty.")
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise ExtractionError(f"The file is larger than the {config.MAX_UPLOAD_MB} MB limit.")
    if b"%PDF-" not in data[:1024]:
        raise ExtractionError("This does not look like a PDF file.")


def _open_validated(data: bytes, max_pages: int | None = None) -> pymupdf.Document:
    """Open a PDF after validating it. The caller must close the returned document."""
    validate_pdf_bytes(data)
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises several error types for bad files
        raise ExtractionError("The PDF could not be opened. It may be corrupted.") from exc

    limit = config.MAX_PAGES if max_pages is None else max_pages
    try:
        if doc.needs_pass:
            raise ExtractionError("The PDF is password-protected. Remove the password and try again.")
        if doc.page_count == 0:
            raise ExtractionError("The PDF has no pages.")
        if doc.page_count > limit:
            raise ExtractionError(f"The PDF has {doc.page_count} pages; the limit is {limit} pages.")
    except ExtractionError:
        doc.close()
        raise
    return doc


def inspect_pdf(data: bytes) -> int:
    """Validate a PDF and return its page count (raises ExtractionError if unusable)."""
    doc = _open_validated(data)
    try:
        return doc.page_count
    finally:
        doc.close()


def extract_pdf(
    source: bytes | str | Path, filename: str | None = None, max_pages: int | None = None
) -> ExtractedDocument:
    """Read a PDF (bytes or path) and return its text, lines and metadata."""
    if isinstance(source, (str, Path)):
        path = Path(source)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ExtractionError(f"Could not read the file: {exc}") from exc
        filename = filename or path.name
    else:
        data = source
        filename = filename or "uploaded.pdf"

    doc = _open_validated(data, max_pages)
    try:
        try:
            pages = tuple(_extract_page(doc[index], index + 1) for index in range(doc.page_count))
        except ExtractionError:
            raise
        except Exception as exc:
            raise ExtractionError("The PDF content could not be read. It may be damaged.") from exc
        metadata = {key: value for key, value in (doc.metadata or {}).items() if value}
    finally:
        doc.close()

    return ExtractedDocument(
        filename=filename,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        metadata=metadata,
        pages=pages,
    )


def _extract_page(page: pymupdf.Page, page_number: int) -> PageContent:
    text = normalize_text(page.get_text("text", sort=True))

    blocks = []
    for x0, y0, x1, y1, block_text, _block_no, block_type in page.get_text("blocks", sort=True):
        if block_type != 0:  # 0 = text, 1 = image
            continue
        cleaned = normalize_text(block_text)
        if cleaned:
            blocks.append(TextBlock(page_number, (round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)), cleaned))

    lines = []
    for block in page.get_text("dict", sort=True)["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            line_text = normalize_line("".join(span.get("text", "") for span in spans))
            if not line_text:
                continue
            x0, y0, x1, y1 = line["bbox"]
            size = max((span.get("size", 0.0) for span in spans), default=0.0)
            lines.append(
                TextLine(page_number, (round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)), line_text, round(size, 1))
            )

    return PageContent(
        page_number=page_number,
        text=text,
        blocks=tuple(blocks),
        width=page.rect.width,
        height=page.rect.height,
        image_count=len(page.get_images(full=True)),
        lines=tuple(lines),
    )

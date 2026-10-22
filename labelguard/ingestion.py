"""Document ingestion: validate an upload, extract its text (OCR if needed) and report what happened.

The returned notes are written for the end user: they explain, in plain words,
anything that limits how much of the label could be read (scans, failed OCR,
low confidence), so the reviewer never assumes text is complete when it is not.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from labelguard import config
from labelguard.extraction.normalize import normalize_text
from labelguard.extraction.ocr import OcrPageResult, OcrStatus, get_ocr_status, ocr_page
from labelguard.extraction.pdf_text import (
    ExtractedDocument,
    ExtractionError,
    PageContent,
    TextBlock,
    extract_pdf,
    inspect_pdf,
)

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class Note:
    """A human-readable processing remark. level is "info" or "warning"."""

    level: str
    message: str


@dataclass(frozen=True)
class IngestionResult:
    document: ExtractedDocument
    notes: tuple[Note, ...]
    ocr_status: OcrStatus


def validate_upload(filename: str, data: bytes) -> int:
    """Check file type, size, format, encryption and page limit. Returns the page count.

    Raises ExtractionError with a user-friendly message if the file is unusable.
    """
    suffix = Path(filename or "").suffix.lower()
    if suffix != ".pdf":
        shown = suffix if suffix else "no file extension"
        raise ExtractionError(f"Unsupported file type ({shown}). Please upload a PDF file.")
    return inspect_pdf(data)


def ingest_pdf(
    data: bytes,
    filename: str,
    *,
    use_ocr: bool = True,
    progress: ProgressCallback | None = None,
) -> IngestionResult:
    """Validate and read a label PDF, falling back to OCR for pages without enough text."""
    say = progress or (lambda message: None)
    validate_upload(filename, data)

    say(f"Reading text from {filename}")
    # The upload is processed from a private temporary folder that is always removed,
    # so no uploaded label is left on disk. (Never trust the uploaded file name for paths.)
    with tempfile.TemporaryDirectory(prefix="labelguard_") as folder:
        path = Path(folder) / "upload.pdf"
        path.write_bytes(data)
        document = extract_pdf(path, filename=filename)

    status = get_ocr_status() if use_ocr else OcrStatus(False, "OCR was turned off for this run.")
    notes: list[Note] = []
    pages = list(document.pages)

    for index, page in enumerate(pages):
        if not page.has_little_text:
            continue
        number = page.page_number

        if not status.available:
            if page.needs_ocr:
                notes.append(Note(
                    "warning",
                    f"Page {number} has little or no selectable text (it is probably a scan) and OCR could not run: "
                    f"{status.message} Text from this page is missing from the analysis.",
                ))
            else:
                notes.append(Note("info", f"Page {number} has little or no text; it may be blank."))
            continue

        say(f"Running OCR on page {number} of {document.page_count}")
        result = ocr_page(data, number, page.width, page.height)
        if result.error:
            notes.append(Note("warning", f"{result.error}. Text from this page is missing from the analysis."))
        elif not result.ok:
            if page.is_blank:
                notes.append(Note("info", f"OCR found no text on page {number}; the page appears to be blank."))
            else:
                notes.append(Note("warning", f"OCR could not read any text on page {number}. Check the image quality."))
        else:
            pages[index] = _page_from_ocr(page, result)
            confidence = result.confidence
            if confidence is not None and confidence < config.OCR_LOW_CONFIDENCE:
                notes.append(Note(
                    "warning",
                    f"Page {number} was read by OCR with low confidence ({confidence:.0f}%). "
                    "Characters may be misread; verify against the source label.",
                ))
            else:
                shown = f" (mean confidence {confidence:.0f}%)" if confidence is not None else ""
                notes.append(Note(
                    "info",
                    f"Page {number} was read by OCR{shown}. OCR can misread characters; verify important values.",
                ))

    return IngestionResult(replace(document, pages=tuple(pages)), tuple(notes), status)


def _page_from_ocr(page: PageContent, result: OcrPageResult) -> PageContent:
    blocks = tuple(TextBlock(line.page, line.bbox, line.text) for line in result.lines)
    return replace(
        page,
        text=normalize_text(result.text),
        blocks=blocks,
        lines=result.lines,
        source="ocr",
        ocr_confidence=result.confidence,
    )

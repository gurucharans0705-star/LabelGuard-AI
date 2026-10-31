"""Document ingestion: validate an upload, extract its text (OCR if needed) and report what happened.

The returned notes are written for the end user: they explain, in plain words,
anything that limits how much of the label could be read (scans, failed OCR,
low confidence), so the reviewer never assumes text is complete when it is not.
"""
from __future__ import annotations

import os
import shutil
import stat
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

TEMP_PREFIX = "labelguard_"
STALE_TEMP_AFTER_S = 3600  # a folder this old cannot belong to a read that is still running


_REPARSE_POINT = 0x400  # Windows FILE_ATTRIBUTE_REPARSE_POINT: set on symbolic links AND on directory junctions


def _is_link_like(info: os.stat_result) -> bool:
    """True for anything that points somewhere else: a POSIX symlink, a Windows symlink or a Windows junction.

    `Path.is_symlink()` is False for a junction, and a junction needs no administrator right to create, so it is the
    kind of link a Windows user can really meet. Takes the result of `lstat` (which follows nothing).
    """
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def remove_stale_temp_folders(max_age_s: float = STALE_TEMP_AFTER_S, *, now: float | None = None) -> int:
    """Delete upload folders left behind by a process that was killed in the middle of reading a label.

    A normal run, and a run that fails with an exception, always removes its own folder; only a hard kill can leave
    one. To be safe this removes a folder only if it is a real directory (not a symlink or Windows junction) whose name starts with the
    prefix, that is older than `max_age_s`, and that holds nothing but the `upload.pdf` this module writes.
    Returns how many folders were removed.
    """
    import time

    current = time.time() if now is None else now
    removed = 0
    for folder in Path(tempfile.gettempdir()).glob(f"{TEMP_PREFIX}*"):
        try:
            info = folder.lstat()  # never follows a link
            if _is_link_like(info) or not stat.S_ISDIR(info.st_mode) or current - info.st_mtime < max_age_s:
                continue
            if {item.name for item in folder.iterdir()} - {"upload.pdf"}:
                continue
            shutil.rmtree(folder)
            removed += 1
        except OSError:
            continue  # in use, not ours to delete, or already gone: leave it
    return removed


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
    with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as folder:
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

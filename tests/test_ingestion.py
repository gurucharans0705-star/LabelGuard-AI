"""Upload validation, malformed files, temp-file cleanup and the OCR fallback (available or not)."""
import tempfile
from pathlib import Path

import pymupdf
import pytest

from labelguard import config
from labelguard.extraction import ocr
from labelguard.extraction.pdf_text import ExtractionError
from labelguard.ingestion import ingest_pdf, validate_upload


def blank_pdf(pages: int = 1) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    return doc.tobytes()


def image_only_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100))
    pixmap.clear_with(255)
    page.insert_image(pymupdf.Rect(50, 50, 250, 250), pixmap=pixmap)
    return doc.tobytes()


# --- Validation and malformed files -------------------------------------------------
def test_valid_pdf_passes_validation(approved_pdf):
    assert validate_upload("label.pdf", approved_pdf.read_bytes()) == 1


def test_rejects_wrong_file_extension(approved_pdf):
    with pytest.raises(ExtractionError, match="Unsupported file type"):
        validate_upload("label.docx", approved_pdf.read_bytes())


def test_rejects_file_without_extension(approved_pdf):
    with pytest.raises(ExtractionError, match="Unsupported file type"):
        validate_upload("label", approved_pdf.read_bytes())


def test_rejects_empty_file():
    with pytest.raises(ExtractionError, match="empty"):
        validate_upload("label.pdf", b"")


def test_rejects_text_renamed_to_pdf():
    with pytest.raises(ExtractionError, match="does not look like a PDF"):
        validate_upload("label.pdf", b"just some text")


def test_rejects_truncated_pdf(approved_pdf):
    with pytest.raises(ExtractionError):
        ingest_pdf(approved_pdf.read_bytes()[:200], "label.pdf")


def test_rejects_corrupted_pdf_body():
    with pytest.raises(ExtractionError):
        validate_upload("label.pdf", b"%PDF-1.7\nthis is not really a pdf body")


def test_rejects_encrypted_pdf():
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret label text")
    protected = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="user", owner_pw="owner")
    with pytest.raises(ExtractionError, match="password"):
        validate_upload("label.pdf", protected)


def test_rejects_oversized_file(approved_pdf, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 0)
    with pytest.raises(ExtractionError, match="limit"):
        validate_upload("label.pdf", approved_pdf.read_bytes())


def test_rejects_too_many_pages(monkeypatch):
    monkeypatch.setattr(config, "MAX_PAGES", 2)
    with pytest.raises(ExtractionError, match="3 pages; the limit is 2"):
        validate_upload("label.pdf", blank_pdf(3))


# --- Digital extraction ----------------------------------------------------------------
def test_digital_pdf_is_read_without_ocr(approved_pdf):
    result = ingest_pdf(approved_pdf.read_bytes(), "approved.pdf")
    assert result.document.ocr_pages == []
    assert "NW-IV200-20" in result.document.full_text
    assert result.notes == ()


def test_blank_pdf_is_reported_as_blank_not_as_an_error():
    result = ingest_pdf(blank_pdf(), "blank.pdf", use_ocr=False)
    assert result.document.char_count == 0
    assert result.document.pages[0].is_blank
    assert any("blank" in note.message for note in result.notes)


def test_temp_files_are_removed_after_ingestion(approved_pdf):
    before = set(Path(tempfile.gettempdir()).glob("labelguard_*"))
    ingest_pdf(approved_pdf.read_bytes(), "approved.pdf")
    with pytest.raises(ExtractionError):
        ingest_pdf(b"not a pdf", "bad.pdf")
    assert set(Path(tempfile.gettempdir()).glob("labelguard_*")) == before


# --- OCR: unavailable, unsuccessful, successful ----------------------------------------------
def test_scanned_page_without_ocr_is_flagged_clearly(scanned_pdf):
    result = ingest_pdf(scanned_pdf.read_bytes(), "scan.pdf", use_ocr=False)
    assert result.document.unreadable_pages == [1]
    warnings = [note for note in result.notes if note.level == "warning"]
    assert warnings and "OCR could not run" in warnings[0].message
    assert "missing from the analysis" in warnings[0].message


def test_ocr_unavailable_when_tesseract_is_missing(scanned_pdf, monkeypatch):
    monkeypatch.setattr(ocr, "find_tesseract", lambda: None)
    status = ocr.get_ocr_status()
    assert not status.available
    assert "not found" in status.message
    result = ingest_pdf(scanned_pdf.read_bytes(), "scan.pdf")  # must not raise
    assert result.document.unreadable_pages == [1]
    assert any(note.level == "warning" and "Tesseract" in note.message for note in result.notes)


def test_ocr_disabled_by_configuration(monkeypatch):
    monkeypatch.setattr(config, "OCR_ENABLED", False)
    status = ocr.get_ocr_status()
    assert not status.available
    assert "switched off" in status.message


def test_ocr_reports_unusable_tesseract_path(monkeypatch):
    monkeypatch.setattr(config, "TESSERACT_CMD", None)
    monkeypatch.setattr(ocr, "find_tesseract", lambda: "/nonexistent/tesseract")
    status = ocr.get_ocr_status()
    assert not status.available
    assert "could not be run" in status.message


def test_ocr_failure_is_reported_not_raised(scanned_pdf, monkeypatch):
    monkeypatch.setattr(ocr, "get_ocr_status", lambda: ocr.OcrStatus(True, "ready"))

    def boom(*args, **kwargs):
        raise RuntimeError("simulated Tesseract crash")

    pytesseract = pytest.importorskip("pytesseract")
    monkeypatch.setattr(pytesseract, "image_to_data", boom)
    page = ocr.ocr_page(scanned_pdf.read_bytes(), 1, 453.0, 368.0)
    assert not page.ok
    assert "OCR failed on page 1" in page.error


def test_unsuccessful_ocr_leaves_a_warning_in_ingestion(scanned_pdf, monkeypatch):
    import labelguard.ingestion as ingestion

    monkeypatch.setattr(ingestion, "get_ocr_status", lambda: ocr.OcrStatus(True, "ready"))
    monkeypatch.setattr(
        ingestion, "ocr_page", lambda *args: ocr.OcrPageResult("", (), None, error="OCR failed on page 1: simulated")
    )
    result = ingest_pdf(scanned_pdf.read_bytes(), "scan.pdf")
    assert result.document.unreadable_pages == [1]
    assert any("OCR failed on page 1" in note.message for note in result.notes)


def test_ocr_that_finds_nothing_in_an_image_warns(monkeypatch):
    import labelguard.ingestion as ingestion

    monkeypatch.setattr(ingestion, "get_ocr_status", lambda: ocr.OcrStatus(True, "ready"))
    monkeypatch.setattr(ingestion, "ocr_page", lambda *args: ocr.OcrPageResult("", (), None))
    result = ingest_pdf(image_only_pdf(), "image.pdf")
    assert any(note.level == "warning" and "could not read any text" in note.message for note in result.notes)


def test_ocr_reads_a_scanned_label(scanned_pdf, tesseract_available):
    if not tesseract_available:
        pytest.skip("Tesseract is not installed on this machine")
    result = ingest_pdf(scanned_pdf.read_bytes(), "scan.pdf")
    assert result.document.ocr_pages == [1]
    text = result.document.full_text
    assert "NW-IV200-20" in text
    assert "A24-0917" in text
    assert result.document.pages[0].ocr_confidence > config.OCR_LOW_CONFIDENCE
    assert any("read by OCR" in note.message for note in result.notes)

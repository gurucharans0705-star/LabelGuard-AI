import pymupdf
import pytest

from labelguard import config
from labelguard.extraction.pdf_render import render_page_png
from labelguard.extraction.pdf_text import ExtractionError, extract_pdf
from labelguard.samples.synthetic_labels import APPROVED, REVISED


def test_extracts_key_fields_from_approved_label(approved_pdf):
    doc = extract_pdf(approved_pdf)
    assert doc.page_count == 1
    assert "NW-IV200-20" in doc.full_text
    assert APPROVED.udi_di in doc.full_text
    assert "Do not use if package is damaged." in doc.full_text
    assert doc.pages_needing_ocr == []


def test_revised_label_shows_expected_changes(revised_pdf):
    text = extract_pdf(revised_pdf).full_text
    assert "NW-IV200-15" in text
    assert REVISED.udi_di in text
    assert "Do not use if package is damaged." not in text
    assert "Contains natural rubber latex." in text


def test_bytes_and_path_give_same_result(approved_pdf):
    from_path = extract_pdf(approved_pdf)
    from_bytes = extract_pdf(approved_pdf.read_bytes(), filename=approved_pdf.name)
    assert from_path.sha256 == from_bytes.sha256
    assert from_path.full_text == from_bytes.full_text
    assert len(from_path.sha256) == 64


def test_blocks_carry_page_and_position_inside_the_page(approved_pdf):
    doc = extract_pdf(approved_pdf)
    page = doc.pages[0]
    assert doc.blocks
    for block in doc.blocks:
        x0, y0, x1, y1 = block.bbox
        assert block.page == 1
        assert 0 <= x0 < x1 <= page.width + 1
        assert 0 <= y0 < y1 <= page.height + 1


def test_rejects_non_pdf_bytes():
    with pytest.raises(ExtractionError, match="PDF"):
        extract_pdf(b"this is plain text, not a pdf")


def test_rejects_empty_file():
    with pytest.raises(ExtractionError, match="empty"):
        extract_pdf(b"")


def test_rejects_corrupted_pdf():
    with pytest.raises(ExtractionError):
        extract_pdf(b"%PDF-1.7\nthis is not really a pdf body")


def test_rejects_oversized_file(approved_pdf, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 0)
    with pytest.raises(ExtractionError, match="limit"):
        extract_pdf(approved_pdf)


def test_rejects_password_protected_pdf():
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret label text")
    protected = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="user", owner_pw="owner")
    with pytest.raises(ExtractionError, match="password"):
        extract_pdf(protected)


def test_image_only_page_is_flagged_for_ocr():
    doc = pymupdf.open()
    page = doc.new_page()
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100))
    pixmap.clear_with(200)
    page.insert_image(pymupdf.Rect(50, 50, 250, 250), pixmap=pixmap)
    result = extract_pdf(doc.tobytes())
    assert result.pages_needing_ocr == [1]
    assert not result.pages[0].is_blank


def test_blank_page_is_flagged_blank_not_scanned():
    doc = pymupdf.open()
    doc.new_page()
    result = extract_pdf(doc.tobytes())
    assert result.pages[0].is_blank
    assert result.pages_needing_ocr == []


def test_render_page_png_returns_png_and_validates_page_number(approved_pdf):
    png = render_page_png(approved_pdf.read_bytes(), 1, zoom=1.0)
    assert png.startswith(b"\x89PNG")
    with pytest.raises(ValueError):
        render_page_png(approved_pdf.read_bytes(), 2)

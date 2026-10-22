"""Completeness observations and the end-to-end pipeline (including the OCR paths)."""
import pytest

from labelguard import config
from labelguard.extraction import ocr
from labelguard.extraction.pdf_text import ExtractionError
from labelguard.pipeline import analyze_label, analyze_pair
from labelguard.validation.checks import CONTEXTUAL_FIELDS, EXPECTED_FIELDS, check_label


# --- Completeness observations ----------------------------------------------------------------------
def test_complete_label_has_no_observations(approved_fields):
    assert check_label(approved_fields) == ()


def test_missing_elements_are_flagged_for_review(incomplete_fields):
    observations = check_label(incomplete_fields)
    review = {o.field for o in observations if o.level == "review"}
    assert review == {"manufacturer", "lot_number", "expiration_date"}
    assert {o.field for o in observations if o.level == "info"} == {"manufacturing_date"}


def test_observations_ask_for_checking_and_never_claim_non_compliance(incomplete_fields):
    for observation in check_label(incomplete_fields):
        text = observation.message.lower()
        assert "not detected" in text
        assert not any(word in text for word in ("violation", "non-compliant", "illegal", "must be"))


def test_expected_and_contextual_field_lists_do_not_overlap():
    assert not set(EXPECTED_FIELDS) & set(CONTEXTUAL_FIELDS)


# --- Pipeline ---------------------------------------------------------------------------------------------
def test_analyze_pair_end_to_end(approved_pdf, revised_pdf):
    steps: list[str] = []
    result = analyze_pair(
        (approved_pdf.read_bytes(), "approved_label_v1.pdf"),
        (revised_pdf.read_bytes(), "revised_label_v2.pdf"),
        progress=steps.append,
    )
    assert result.comparison.changed_fields == 5
    assert result.approved.document.filename == "approved_label_v1.pdf"
    assert result.revised.document.page_count == 1
    assert steps[0] == "Processing the approved label" and steps[-1] == "Comparing the two labels"


def test_error_message_says_which_label_is_unusable(approved_pdf):
    good = (approved_pdf.read_bytes(), "good.pdf")
    with pytest.raises(ExtractionError, match=r"^Approved label \(bad\.pdf\): .*does not look like a PDF"):
        analyze_pair((b"not a pdf", "bad.pdf"), good)
    with pytest.raises(ExtractionError, match=r"^Revised label \(notes\.txt\): Unsupported file type \(\.txt\)"):
        analyze_pair(good, (b"hello", "notes.txt"))


def test_empty_pdf_is_analysable_and_yields_observations_not_a_crash():
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    analysis = analyze_label(doc.tobytes(), "blank.pdf", use_ocr=False)
    assert analysis.fields.detected_count == 0
    assert len([o for o in analysis.observations if o.level == "review"]) == len(EXPECTED_FIELDS)


# --- OCR paths through the whole pipeline ------------------------------------------------------------------
def test_scanned_label_without_ocr_reports_missing_text_and_no_fields(scanned_pdf):
    analysis = analyze_label(scanned_pdf.read_bytes(), "scan.pdf", use_ocr=False)
    assert analysis.fields.detected_count == 0  # nothing is invented from an unreadable page
    assert analysis.document.unreadable_pages == [1]
    assert any(n.level == "warning" and "missing from the analysis" in n.message for n in analysis.ingestion.notes)


def test_scanned_label_when_tesseract_is_missing_still_completes(scanned_pdf, monkeypatch):
    monkeypatch.setattr(ocr, "find_tesseract", lambda: None)
    analysis = analyze_label(scanned_pdf.read_bytes(), "scan.pdf")
    assert analysis.fields.detected_count == 0
    assert not analysis.ingestion.ocr_status.available
    assert any("Tesseract" in n.message for n in analysis.ingestion.notes)


def test_scanned_label_read_by_ocr_matches_the_digital_original(approved_pdf, scanned_pdf, tesseract_available):
    if not tesseract_available:
        pytest.skip("Tesseract is not installed on this machine")
    result = analyze_pair(
        (approved_pdf.read_bytes(), "digital.pdf"), (scanned_pdf.read_bytes(), "scan.pdf")
    )
    assert result.revised.document.ocr_pages == [1]
    # Every field read from the scan must equal the digital value, even if OCR splits lines differently.
    assert result.comparison.changed_fields == 0
    assert result.comparison.matching_fields == 10
    assert result.revised.ingestion.ocr_status.available
    assert result.revised.document.pages[0].ocr_confidence > config.OCR_LOW_CONFIDENCE

"""End-to-end verification tests: each one renders real PDFs and runs verify_pair."""
from dataclasses import replace

import pytest

from labelguard.rules.models import RESULT_FLAGGED, RESULT_NOT_EVALUATED, Evidence, Finding
from labelguard.samples.synthetic_labels import APPROVED as A
from labelguard.samples.synthetic_labels import render_label_pdf
from labelguard.verification import verify_pair


@pytest.fixture(scope="module")
def pdf_of(tmp_path_factory):
    folder = tmp_path_factory.mktemp("specs")
    cache = {}

    def make(spec):
        if spec not in cache:
            cache[spec] = render_label_pdf(spec, folder / f"s{len(cache)}.pdf").read_bytes()
        return cache[spec]

    return make


@pytest.fixture(scope="module")
def run(pdf_of):
    def go(revised_spec, **options):
        return verify_pair((pdf_of(A), "a.pdf"), (pdf_of(revised_spec), "r.pdf"), **options)

    return go


def flagged(result):
    return {f.rule_id for f in result.report.flagged}


def test_unchanged_label_has_no_findings(run):
    result = run(A)
    assert result.report.findings == ()
    assert result.visual.pages and all(p.status == "identical" for p in result.visual.pages)


def test_changed_identifier(run):
    result = run(replace(A, ref="NW-IV200-99"))
    assert flagged(result) == {"R-ID-001"}
    finding = result.report.flagged[0]
    assert finding.severity == "critical_review"
    assert "NW-IV200-20" in finding.approved_value and "NW-IV200-99" in finding.revised_value
    assert finding.evidence_complete and finding.pages == (1,)


def test_missing_field(run):
    result = run(replace(A, lot=None))
    assert flagged(result) == {"R-REQ-005"}


PAGE2 = ["Instructions for use", "Open the pouch.", "Connect the line.", "Discard after use."]


def test_multi_page_unchanged_and_modified(pdf_of):
    two = replace(A, extra_pages=(tuple(PAGE2),))
    same = verify_pair((pdf_of(two), "a.pdf"), (pdf_of(two), "r.pdf"))
    assert same.report.findings == ()
    assert len(same.visual.pages) == 2
    edited = replace(A, extra_pages=((*PAGE2[:2], "Connect the tubing.", PAGE2[3]),))
    findings = verify_pair((pdf_of(two), "a.pdf"), (pdf_of(edited), "r.pdf")).report.flagged
    assert findings and all(f.pages == (2,) for f in findings)
    assert not any(f.rule_id == "R-PAGE-001" for f in findings)


def test_removed_warning(run):
    result = run(replace(A, warnings=A.warnings[:1] + A.warnings[2:]))
    assert flagged(result) == {"R-WARN-001"}
    finding = result.report.flagged[0]
    assert "package is damaged" in (finding.approved_value or "")
    assert finding.revised_value in (None, "")


def test_added_harmless_content_is_low_priority(run):
    result = run(replace(A, extra_lines=("Distributed by Example Distribution Co.",)))
    assert [(f.rule_id, f.severity) for f in result.report.findings] == [("R-TEXT-001", "low")]


def test_reordered_text_is_not_a_change(run):
    result = run(replace(A, warnings=(A.warnings[1], A.warnings[0]) + A.warnings[2:]))
    assert result.report.findings == ()


def test_page_count_mismatch(run):
    result = run(replace(A, extra_pages=(("Instructions", "Open the pouch.", "Connect the line."),)))
    assert flagged(result) == {"R-PAGE-001", "R-TEXT-001"}
    page_finding = next(f for f in result.report.flagged if f.rule_id == "R-PAGE-001")
    assert "1" in page_finding.approved_value and "2" in page_finding.revised_value
    assert result.visual.unpaired_revised == (2,)


def test_incomplete_evidence_is_visible():
    bare = Finding("X-1", "X", "x", "text", "low", RESULT_FLAGGED, "t", "d", "text")
    assert not bare.evidence_complete  # no evidence at all
    no_page = replace(bare, evidence=(Evidence("revised", None, "some quote"),))
    assert not no_page.evidence_complete  # a quote without a page
    assert replace(bare, evidence=(Evidence("both", None, "page count 1 vs 2", kind="measurement"),)).evidence_complete
    assert replace(bare, evidence=(Evidence("revised", 2, "quote"),)).evidence_complete


def test_findings_are_deterministic(run):
    spec = replace(A, ref="NW-IV200-99", lot="B25-0001")
    first = [f.id for f in run(spec).report.findings]
    second = [f.id for f in run(spec).report.findings]
    assert first == second and len(set(first)) == len(first)


def test_scan_vs_digital_flags_nothing_and_says_visual_not_verified(approved_pdf, scanned_pdf):
    result = verify_pair((approved_pdf.read_bytes(), "a.pdf"), (scanned_pdf.read_bytes(), "s.pdf"))
    assert result.report.flagged == ()
    assert all(f.result == RESULT_NOT_EVALUATED and f.severity == "informational" for f in result.report.findings)


def test_unavailable_barcode_tool_is_reported_not_passed(run, monkeypatch):
    from labelguard.vision import barcode

    monkeypatch.setattr(barcode, "_load_decoder", lambda: None)
    result = run(A)
    assert result.approved_barcodes.status == "unavailable" and not result.approved_barcodes.decoded
    assert not any(f.rule_id in ("R-ID-003", "R-ID-004") and f.result == RESULT_FLAGGED for f in result.report.findings)
    outcome = {o.rule_id: o.outcome for o in result.report.outcomes}
    assert outcome["R-ID-004"] != "passed"


def test_unavailable_vision_tool_is_reported_not_passed(run, monkeypatch):
    from labelguard.vision import compare, images

    monkeypatch.setattr(images, "vision_status", lambda: (False, "OpenCV is not installed (test)."))
    monkeypatch.setattr(compare, "vision_status", lambda: (False, "OpenCV is not installed (test)."))
    result = run(replace(A, band_color="#8A2B2B"))
    assert result.visual.status == "unavailable"
    assert flagged(result) == set()
    assert {o.rule_id: o.outcome for o in result.report.outcomes}["R-ART-001"] == "not_evaluated"


def test_bad_rules_file_fails_before_analysis(pdf_of, tmp_path):
    from labelguard.rules.loader import RuleConfigError

    broken = tmp_path / "rules.toml"
    broken.write_text("not = [valid", encoding="utf-8")
    with pytest.raises(RuleConfigError):
        verify_pair((pdf_of(A), "a.pdf"), (pdf_of(A), "r.pdf"), rules_path=broken)


# --- scanned documents when the text cannot be read ---------------------------------------------------------
def _ocr_unavailable(monkeypatch):
    """Simulate a machine without Tesseract, whatever this machine has."""
    from labelguard import config
    from labelguard.extraction import ocr

    monkeypatch.setattr(config, "OCR_ENABLED", False)
    monkeypatch.setattr(ocr, "get_ocr_status", lambda: ocr.OcrStatus(False, "Tesseract is not installed (test)."))


@pytest.mark.parametrize("use_ocr", [True, False])
def test_scan_vs_digital_without_readable_text_flags_nothing(approved_pdf, scanned_pdf, monkeypatch, use_ocr):
    _ocr_unavailable(monkeypatch)  # OCR cannot run, so the scan has no text, with the option on or off
    result = verify_pair((approved_pdf.read_bytes(), "a.pdf"), (scanned_pdf.read_bytes(), "s.pdf"), use_ocr=use_ocr)
    assert result.report.flagged == (), [f.title for f in result.report.flagged]  # no invented removals
    assert result.analysis.revised.document.char_count == 0  # the premise: nothing could be read
    unreadable = [f for f in result.report.findings if f.rule_id == "R-READ-001"]
    assert len(unreadable) == 1 and unreadable[0].result == RESULT_NOT_EVALUATED
    assert "Revised label" in unreadable[0].title and "not a statement that the label is empty" in unreadable[0].description
    assert all(f.severity == "informational" for f in result.report.findings)  # nothing claims a discrepancy
    outcomes = {o.rule_id: o.outcome for o in result.report.outcomes}
    for rule_id in ("R-REQ-001", "R-WARN-001", "R-TEXT-002", "R-READ-001"):
        assert outcomes[rule_id] == "not_evaluated", rule_id  # never "passed"
    assert outcomes["R-PAGE-001"] == "passed"  # rules that do not need the text still run


def test_unreadable_side_does_not_hide_the_readable_side(tmp_path, monkeypatch):
    # The approved label has an invalid UDI check digit; the revised label is an unreadable scan of it.
    from labelguard.samples.synthetic_labels import make_scanned_copy

    _ocr_unavailable(monkeypatch)
    bad = render_label_pdf(replace(A, udi_text="(01)08901234567899"), tmp_path / "bad.pdf").read_bytes()
    result = verify_pair((bad, "a.pdf"), (make_scanned_copy(bad), "s.pdf"))
    # the approved label's own problems (bad check digit; printed UDI disagrees with its barcode) are still reported
    assert flagged(result) == {"R-ID-004", "R-ID-005"}
    assert all(item.source == "approved" for f in result.report.flagged for item in f.evidence)
    # a rule that could only be checked on one label must not read as a pass
    outcomes = {o.rule_id: o for o in result.report.outcomes}
    assert outcomes["R-REQ-001"].outcome == "not_evaluated" and "approved label only" in outcomes["R-REQ-001"].note


def test_real_findings_survive_when_only_some_pages_are_unreadable(revised_pdf, scanned_pdf, tmp_path, monkeypatch):
    import pymupdf

    _ocr_unavailable(monkeypatch)
    mixed = pymupdf.open(stream=revised_pdf.read_bytes(), filetype="pdf")  # page 1: digital v2 label
    mixed.insert_pdf(pymupdf.open(stream=scanned_pdf.read_bytes(), filetype="pdf"))  # page 2: image only
    data = mixed.tobytes()
    base = render_label_pdf(A, tmp_path / "a.pdf").read_bytes()
    result = verify_pair((base, "a.pdf"), (data, "r.pdf"))
    assert {"R-ID-001", "R-WARN-001"} <= flagged(result)  # genuine differences on page 1 are still reported
    note = next(f for f in result.report.findings if f.rule_id == "R-READ-001")
    assert "page(s) 2" in note.title and "Fields or warnings that sit only on those pages" in note.description


def test_digital_pdfs_are_unaffected_by_the_readability_check(run):
    result = run(replace(A, ref="NW-IV200-99"))
    assert flagged(result) == {"R-ID-001"}
    assert {o.rule_id: o.outcome for o in result.report.outcomes}["R-READ-001"] == "passed"


def test_a_blank_page_is_not_treated_as_unreadable():
    # No text and no image: a genuinely empty page, which is a real difference, not a reading problem.
    from labelguard.extraction.pdf_text import PageContent

    blank = PageContent(1, "", (), 595, 842, image_count=0)
    scan = PageContent(1, "", (), 595, 842, image_count=1)
    assert not blank.needs_ocr and scan.needs_ocr


def test_low_ocr_confidence_is_reported_without_suppressing_findings(approved_pdf):
    from dataclasses import replace as clone

    from labelguard.extraction.pdf_text import extract_pdf
    from labelguard.fields.extractor import extract_fields
    from labelguard.rules.engine import RuleContext, evaluate_rules
    from labelguard.rules.loader import load_rules
    from labelguard.comparison.engine import compare_labels

    document = extract_pdf(approved_pdf.read_bytes(), "a.pdf")
    shaky = clone(document, pages=tuple(clone(p, source="ocr", ocr_confidence=35.0) for p in document.pages))
    fields = extract_fields(document)
    context = RuleContext(fields, fields, document, shaky,
                          compare_labels(document.all_lines, fields, document.all_lines, fields))
    report = evaluate_rules(load_rules(), context)
    low = [f for f in report.findings if f.rule_id == "R-READ-001"]
    assert len(low) == 1 and "35%" in low[0].description and low[0].result == RESULT_NOT_EVALUATED
    assert report.flagged == ()

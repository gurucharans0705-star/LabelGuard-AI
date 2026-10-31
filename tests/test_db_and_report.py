"""SQLite run log and the Markdown comparison report."""
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from labelguard import config
from labelguard.db import database
from labelguard.pipeline import analyze_pair
from labelguard.reporting.export import comparison_to_markdown, field_rows


@pytest.fixture(scope="module")
def sample_analysis(tmp_path_factory):
    from labelguard.samples.synthetic_labels import APPROVED_NAME, REVISED_NAME, ensure_sample_files

    files = ensure_sample_files(tmp_path_factory.mktemp("report_samples"))
    return analyze_pair(
        (files[APPROVED_NAME].read_bytes(), "approved_label_v1.pdf"),
        (files[REVISED_NAME].read_bytes(), "revised_label_v2.pdf"),
    )


# --- Run log ----------------------------------------------------------------------------------------
def test_run_is_saved_and_read_back(sample_analysis):
    run_id = database.save_run(sample_analysis)
    (run,) = database.list_runs()
    assert run.id == run_id == 1
    assert (run.approved_filename, run.revised_filename) == ("approved_label_v1.pdf", "revised_label_v2.pdf")
    assert run.approved_sha256 == sample_analysis.approved.document.sha256
    assert (run.fields_matching, run.fields_changed, run.fields_undetected) == (5, 5, 0)
    assert (run.text_modified, run.text_added, run.text_removed) == (5, 0, 1)
    assert run.ocr_pages == 0
    assert datetime.fromisoformat(run.created_at).tzinfo == timezone.utc


def test_newest_run_comes_first_and_limit_is_respected(sample_analysis):
    ids = [database.save_run(sample_analysis) for _ in range(4)]
    assert [run.id for run in database.list_runs()] == ids[::-1]
    assert [run.id for run in database.list_runs(limit=2)] == ids[:1:-1]


def test_empty_database_lists_no_runs():
    assert database.list_runs() == []


def test_only_extracted_fields_are_stored_not_the_full_label_text(sample_analysis):
    # Day 1/2 stored no label content at all. From Day 3 the extracted field values and the findings'
    # evidence quotes are stored (the audit trail needs them) but never the document body or the PDF.
    database.save_run(sample_analysis)
    raw = config.DB_PATH.read_bytes()
    document = sample_analysis.approved.document
    assert document.full_text.encode() not in raw
    assert b"%PDF" not in raw
    stored = json.dumps(database.load_job_fields(1)).encode()
    unextracted = [line.text for line in document.all_lines if line.text.encode() not in stored and len(line.text) > 20]
    assert unextracted, "the sample should have label lines that are not extracted fields"
    for line in unextracted:
        assert line.encode() not in raw, line
    assert b"Northwind" in raw  # the extracted manufacturer field is stored on purpose


def test_unusable_database_raises_sqlite_error_not_something_obscure(sample_analysis, tmp_path):
    with pytest.raises(sqlite3.Error):
        database.save_run(sample_analysis, db_path=tmp_path)  # a folder, not a file


def test_database_location_can_be_given_explicitly(sample_analysis, tmp_path):
    custom = tmp_path / "nested" / "custom.db"
    database.save_run(sample_analysis, db_path=custom)
    assert custom.exists() and len(database.list_runs(db_path=custom)) == 1
    assert database.list_runs() == []  # the default database was untouched


# --- Markdown report -------------------------------------------------------------------------------
def test_report_contains_files_changes_and_evidence(sample_analysis):
    from labelguard.safe_text import escape_markdown

    report = comparison_to_markdown(sample_analysis, generated_at=datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc))
    assert "Generated: 2026-01-02 03:04 UTC" in report
    for expected in (
        # file names are untrusted text, so the report writes them Markdown-escaped (see test_untrusted_output.py)
        escape_markdown("approved_label_v1.pdf"), escape_markdown("revised_label_v2.pdf"),
        sample_analysis.approved.document.sha256, sample_analysis.revised.document.sha256,
        "NW-IV200-20", "NW-IV200-15",
        "Missing from revised label", "Do not use if package is damaged.",
        '"REF: NW-IV200-20"',  # evidence quote, with page
        "page 1",
    ):
        assert expected in report, expected


def test_report_never_claims_compliance(sample_analysis):
    report = comparison_to_markdown(sample_analysis).lower()
    assert "does not establish or certify regulatory compliance" in report
    for claim in ("is compliant", "are compliant", "complies with", "passed compliance", "guarantee"):
        assert claim not in report


def test_report_for_identical_labels_says_so(approved_pdf):
    data = approved_pdf.read_bytes()
    report = comparison_to_markdown(analyze_pair((data, "a.pdf"), (data, "b.pdf")))
    assert "The text of the two labels is identical." in report
    assert "Fields with differences: 0" in report


def test_report_includes_processing_warnings_for_unreadable_pages(approved_pdf, scanned_pdf):
    result = analyze_pair((approved_pdf.read_bytes(), "a.pdf"), (scanned_pdf.read_bytes(), "scan.pdf"), use_ocr=False)
    report = comparison_to_markdown(result)
    assert "## Processing warnings" in report
    assert "missing from the analysis" in report
    assert "Product identifier (REF / catalog / model) was not detected" in report  # observation for the scan


def test_pipe_characters_cannot_break_report_tables(approved_pdf):
    from labelguard.reporting.export import _cell

    assert _cell("a | b\nc") == "a \\| b c"


def test_field_rows_cover_every_field(sample_analysis):
    rows = field_rows(sample_analysis.comparison)
    assert {row["Field"] for row in rows} >= {"Product name", "Lot / batch number", "Sterilization information"}
    assert {row["Result"] for row in rows} == {"Exact match", "Modified", "Missing from revised label"}

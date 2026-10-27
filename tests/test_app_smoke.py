"""End-to-end tests of the Streamlit app, driven through Streamlit's AppTest harness.

These exercise the real page code, including the file uploaders, without a browser.
"""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")
PDF = "application/pdf"


def new_app() -> AppTest:
    return AppTest.from_file(APP_PATH, default_timeout=90).run()


def open_page(at: AppTest, page: str) -> AppTest:
    return at.radio(key="nav").set_value(page).run()


def upload(at: AppTest, role: str, name: str, data: bytes) -> AppTest:
    at.file_uploader(key=f"uploader_{role}_0").upload(name, data, PDF).run()
    return at


def texts(elements) -> str:
    return " ".join(element.value for element in elements)


def test_dashboard_loads_with_navigation():
    at = new_app()
    assert not at.exception
    assert at.radio(key="nav").options == [
        "Dashboard", "Upload & Analyze", "Findings", "Visual Comparison", "Results", "AI Explanations", "Review Queue",
        "Change Impact", "Product History", "Audit History", "Rules"]
    assert {m.label for m in at.metric} >= {"Approved label", "Revised label", "Analysis", "OCR engine"}


def test_results_page_without_analysis_guides_the_user():
    at = open_page(new_app(), "Results")
    assert not at.exception
    assert "No analysis yet" in texts(at.info)


def test_start_button_disabled_until_two_valid_files_are_loaded():
    at = open_page(new_app(), "Upload & Analyze")
    assert at.button(key="start_analysis").disabled


def test_both_labels_can_be_uploaded_and_analysed(approved_pdf, revised_pdf):
    at = open_page(new_app(), "Upload & Analyze")
    upload(at, "approved", "approved_label_v1.pdf", approved_pdf.read_bytes())
    assert not at.exception
    assert at.button(key="start_analysis").disabled  # revised label still missing

    upload(at, "revised", "revised_label_v2.pdf", revised_pdf.read_bytes())
    assert not at.exception
    assert "passed validation" in texts(at.success)
    assert not at.button(key="start_analysis").disabled

    at.button(key="start_analysis").click().run()
    assert not at.exception
    assert at.radio(key="nav").value == "Findings"
    analysis = at.session_state["analysis"]
    assert analysis.comparison.changed_fields == 5
    assert analysis.approved.document.filename == "approved_label_v1.pdf"
    assert analysis.revised.document.filename == "revised_label_v2.pdf"
    assert any(df.value is not None for df in at.dataframe)


def test_invalid_upload_shows_clear_error_and_blocks_analysis(approved_pdf):
    at = open_page(new_app(), "Upload & Analyze")
    upload(at, "approved", "broken.pdf", b"this is not a pdf at all")
    upload(at, "revised", "revised.pdf", approved_pdf.read_bytes())
    assert not at.exception
    assert "does not look like a PDF" in texts(at.error)
    assert at.button(key="start_analysis").disabled


def test_samples_can_be_loaded_and_compared():
    at = open_page(new_app(), "Upload & Analyze")
    at.button(key="load_samples").click().run()
    assert not at.exception
    at.button(key="start_analysis").click().run()
    assert not at.exception
    assert at.radio(key="nav").value == "Findings"
    assert at.session_state["analysis"].comparison.changed_fields == 5


def test_analysis_is_logged_and_shown_on_the_dashboard():
    at = open_page(new_app(), "Upload & Analyze")
    at.button(key="load_samples").click().run()
    at.button(key="start_analysis").click().run()
    assert at.session_state["run_id"] == 1
    open_page(at, "Results")
    assert "run #1" in " ".join(caption.value for caption in at.caption)
    open_page(at, "Dashboard")
    assert not at.exception
    assert "Recent analyses" in texts(at.subheader)


def test_a_broken_run_log_never_blocks_the_analysis(tmp_path, monkeypatch):
    from labelguard import config

    monkeypatch.setattr(config, "DB_PATH", tmp_path)  # a folder, so SQLite cannot open it
    at = open_page(new_app(), "Upload & Analyze")
    at.button(key="load_samples").click().run()
    at.button(key="start_analysis").click().run()
    assert not at.exception
    assert at.session_state["analysis"].comparison.changed_fields == 5
    assert "run_id" not in at.session_state
    assert "this run was not logged" in texts(at.warning)
    open_page(at, "Dashboard")
    assert not at.exception


def test_changing_a_file_discards_the_old_analysis():
    at = open_page(new_app(), "Upload & Analyze")
    at.button(key="load_samples").click().run()
    at.button(key="start_analysis").click().run()
    assert at.session_state["analysis"] is not None
    open_page(at, "Upload & Analyze")
    at.button(key="remove_revised").click().run()
    assert "analysis" not in at.session_state


@pytest.mark.parametrize("page", ["Dashboard", "Upload & Analyze", "Findings", "Visual Comparison", "Results", "Rules"])
def test_every_page_renders_without_errors(page):
    at = open_page(new_app(), page)
    assert not at.exception


# --- Day 2 pages ---------------------------------------------------------------------------------------
def analysed_app() -> AppTest:
    at = open_page(new_app(), "Upload & Analyze")
    at.button(key="load_samples").click().run()
    at.button(key="start_analysis").click().run()
    assert not at.exception
    return at


def test_pages_show_empty_states_before_any_analysis():
    for page in ("Findings", "Visual Comparison"):
        at = open_page(new_app(), page)
        assert not at.exception
        assert "No analysis yet" in texts(at.info)


def test_findings_page_shows_counts_table_and_details():
    at = analysed_app()
    assert at.radio(key="nav").value == "Findings"
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Critical review"] == "3" and metrics["High"] == "3" and metrics["Medium"] == "2"
    assert len(at.dataframe) >= 1
    assert len(at.expander) >= 8


def test_filters_narrow_the_findings():
    at = analysed_app()
    at.multiselect(key="filter_severity").set_value(["critical_review"]).run()
    assert not at.exception
    assert "Showing **3** of 8" in texts(at.markdown)
    at.multiselect(key="filter_severity").set_value([]).run()
    assert "No finding matches" in texts(at.info)


def test_review_buttons_need_a_named_reviewer_and_a_rationale():
    """Day 3 rules (these replace the one-click Day 2 flow): a named person decides, and gives a reason to resolve."""
    at = analysed_app()
    finding = at.session_state["verification"].report.flagged[0]
    assert at.button(key=f"res_{finding.id}").disabled and at.button(key=f"start_{finding.id}").disabled  # no name yet
    at.text_input(key="reviewer_name").set_value("QA Tester").run()
    assert not at.button(key=f"res_{finding.id}").disabled
    at.button(key=f"res_{finding.id}").click().run()  # no rationale
    assert not at.exception and "rationale" in texts(at.error)
    assert finding.id not in at.session_state["reviews"]  # nothing was saved
    at.text_input(key=f"note_{finding.id}").set_value("covered by change request CR-7").run()
    at.button(key=f"res_{finding.id}").click().run()
    assert not at.exception and at.session_state["reviews"][finding.id].status == "resolved"


def test_start_resolve_and_reopen_are_saved_with_reviewer_and_rationale():
    from labelguard.db import database

    at = analysed_app()
    finding = at.session_state["verification"].report.flagged[0]
    at.text_input(key="reviewer_name").set_value("QA Tester").run()
    at.button(key=f"start_{finding.id}").click().run()
    assert at.session_state["reviews"][finding.id].status == "under_review"
    at.text_input(key=f"note_{finding.id}").set_value("seen by QA").run()
    at.button(key=f"res_{finding.id}").click().run()
    record = at.session_state["reviews"][finding.id]
    assert (record.status, record.rationale, record.reviewer) == ("resolved", "seen by QA", "QA Tester")
    stored = database.get_reviews(
        at.session_state["analysis"].approved.document.sha256, at.session_state["analysis"].revised.document.sha256)
    assert stored[finding.id].status == "resolved" and stored[finding.id].reviewer == "QA Tester"
    at.button(key=f"reopen_{finding.id}").click().run()
    assert at.session_state["reviews"][finding.id].status == "pending"
    history = database.review_history(record_hash(at, "approved"), record_hash(at, "revised"), finding.id)
    assert [e.to_status for e in history] == ["under_review", "resolved", "pending"]  # the resolved step is kept


def record_hash(at, side):
    return getattr(at.session_state["analysis"], side).document.sha256


def test_decisions_survive_a_new_session_for_the_same_files():
    at = analysed_app()
    finding = at.session_state["verification"].report.flagged[0]
    at.text_input(key="reviewer_name").set_value("QA Tester").run()
    at.text_input(key=f"note_{finding.id}").set_value("ok per CR-7").run()
    at.button(key=f"res_{finding.id}").click().run()
    again = analysed_app()
    assert again.session_state["reviews"][finding.id].status == "resolved"
    assert again.session_state["reviews"][finding.id].rationale == "ok per CR-7"


def test_unchanged_pair_shows_the_no_findings_state():
    at = open_page(new_app(), "Upload & Analyze")
    at.selectbox(key="sample_revised").set_value("Approved label (v1)").run()
    at.button(key="load_samples").click().run()
    at.button(key="start_analysis").click().run()
    assert not at.exception
    assert "No findings" in texts(at.success)
    assert "not a statement that the label is compliant" in texts(at.caption)


def test_visual_and_rules_pages_render_after_analysis():
    at = analysed_app()
    open_page(at, "Visual Comparison")
    assert not at.exception
    assert len(at.selectbox(key="visual_page").options) >= 1
    open_page(at, "Rules")
    assert not at.exception
    assert len(at.dataframe) >= 1


def test_broken_rules_file_is_shown_not_crashed(monkeypatch, tmp_path):
    from labelguard import config

    bad = tmp_path / "rules.toml"
    bad.write_text("[[rule]]\nid = 'X'\nseverity = 'nope'\n", encoding="utf-8")
    monkeypatch.setattr(config, "RULES_PATH", bad)
    at = open_page(new_app(), "Rules")
    assert not at.exception
    assert "problem(s)" in texts(at.error)
    at = open_page(at, "Upload & Analyze")
    at.button(key="load_samples").click().run()
    at.button(key="start_analysis").click().run()
    assert not at.exception
    assert "problem(s)" in texts(at.error) and "analysis" not in at.session_state


def test_scanned_revised_label_without_ocr_shows_not_verified_not_false_findings():
    # Works the same on machines with and without Tesseract: OCR is switched off for this run.
    at = open_page(new_app(), "Upload & Analyze")
    at.selectbox(key="sample_revised").set_value("Scanned copy of approved label (image only)").run()
    at.button(key="load_samples").click().run()
    if not at.checkbox(key="use_ocr").disabled:  # it is disabled (and already off) when Tesseract is missing
        at.checkbox(key="use_ocr").set_value(False).run()
    assert at.checkbox(key="use_ocr").value is False
    at.button(key="start_analysis").click().run()
    assert not at.exception
    assert at.radio(key="nav").value == "Findings"
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Discrepancies flagged"] == "0" and int(metrics["Not verified (check manually)"]) >= 1
    assert at.session_state["verification"].report.flagged == ()
    assert any("text not readable" in e.label for e in at.expander)
    for page in ("Dashboard", "Findings", "Visual Comparison", "Results", "Rules"):
        open_page(at, page)
        assert not at.exception, page

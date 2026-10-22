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
    assert at.radio(key="nav").options == ["Dashboard", "Upload & Analyze", "Results"]
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
    assert at.radio(key="nav").value == "Results"
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
    assert at.radio(key="nav").value == "Results"
    assert at.session_state["analysis"].comparison.changed_fields == 5


def test_analysis_is_logged_and_shown_on_the_dashboard():
    at = open_page(new_app(), "Upload & Analyze")
    at.button(key="load_samples").click().run()
    at.button(key="start_analysis").click().run()
    assert at.session_state["run_id"] == 1
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
    assert "could not be added to the run log" in texts(at.warning)
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


@pytest.mark.parametrize("page", ["Dashboard", "Upload & Analyze", "Results"])
def test_every_page_renders_without_errors(page):
    at = open_page(new_app(), page)
    assert not at.exception

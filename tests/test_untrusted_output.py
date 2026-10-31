"""Untrusted text (label contents, file names, reviewer names) must be displayed and exported as DATA.

Found during Day 4 exploratory testing, measured in Chromium before the fix: opening a finding whose label text
contained a Markdown image made the browser request the attacker's URL, and bare URLs became clickable links. CSV
exports also wrote cells such as =HYPERLINK(...) that a spreadsheet would run. These tests keep that fixed.
"""
import re
from dataclasses import replace

import pytest
from streamlit.testing.v1 import AppTest

from labelguard.db import audit, database
from labelguard.reporting.export import field_csv, verification_to_markdown
from labelguard.safe_text import csv_cell, escape_markdown, safe_csv
from labelguard.samples.synthetic_labels import APPROVED, render_label_pdf
from labelguard.verification import verify_pair

from test_app_smoke import APP_PATH, PDF, open_page

IMAGE = "![t](https://evil.example/pixel.png)"
LINK = "[click here](https://evil.example/x)"
BARE = "https://evil.example/bare www.evil.example mail@evil.example"
# What a Markdown renderer would turn into an image, a link or an automatic link.
ACTIVE = re.compile(r"(?<!\\)!\[|(?<!\\)\]\(|(?i:\bhttps?://)|(?i:\bwww\.)|[\w.+-]+@[\w-]+\.")


# --- the two helpers ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", [IMAGE, LINK, BARE, f"{IMAGE} {LINK} {BARE}", "<img src=x onerror=alert(1)>", "<https://evil.example>"])
def test_hostile_markdown_is_made_inert(text):
    assert not ACTIVE.search(escape_markdown(text)), escape_markdown(text)


def test_ordinary_label_text_is_not_changed_for_the_reader():
    for text in ["Store between 15 and 30 degrees C. Keep dry.", "REF: NW-IV200-20 (20 drops/mL)", "Do not use if package is damaged.",
                 "Rx only. Federal law restricts this device to sale by or on the order of a physician."]:
        assert escape_markdown(text) == text


def test_line_breaks_cannot_start_a_heading_or_list_and_none_is_empty():
    assert "\n" not in escape_markdown("a\n# heading\n- item\n> quote")
    assert not escape_markdown("a\n# heading").startswith("#") and escape_markdown(None) == ""


@pytest.mark.parametrize("value", ["=1+1", "+cmd|' /C calc'!A0", "-2+3", "@SUM(A1)", "\t=1", "\r=1"])
def test_formula_like_cells_are_defused(value):
    assert csv_cell(value) == "'" + value


@pytest.mark.parametrize("value", ["NW-IV200-20", "Store at 15 C", "", 12, 3.5, None])
def test_other_cells_are_left_alone(value):
    assert csv_cell(value) == value


def test_safe_csv_defuses_text_cells_only():
    import pandas as pd

    text = safe_csv(pd.DataFrame({"a": ["=1+1", "ok"], "n": [-5, 2]}))
    assert "'=1+1" in text and "-5" in text and "'-5" not in text


# --- a hostile label, through the real pipeline ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def hostile_pair(tmp_path_factory):
    folder = tmp_path_factory.mktemp("hostile")
    hostile = replace(APPROVED, product_name='=HYPERLINK("https://evil.example","NW-IV200 Set")', manufacturer="@SUM(1+1) Northwind",
                      warnings=APPROVED.warnings + (f"{IMAGE} {LINK} {BARE}", "+cmd|' /C calc'!A0"))
    approved = render_label_pdf(APPROVED, folder / "a.pdf").read_bytes()
    revised = render_label_pdf(hostile, folder / "r.pdf").read_bytes()
    return approved, revised, verify_pair((approved, "=SUM(A1).pdf"), (revised, "@evil.pdf"))


def test_the_label_text_really_reaches_the_findings(hostile_pair):
    """Guards the other tests: if the hostile text were dropped earlier, they would pass for the wrong reason."""
    text = " ".join((f.description or "") + (f.revised_value or "") for f in hostile_pair[2].report.findings)
    assert "evil.example/pixel.png" in text and "+cmd|" in text


def test_the_markdown_report_contains_no_active_content(hostile_pair):
    report = verification_to_markdown(hostile_pair[2])
    assert "evil.example" in report  # the text is shown ...
    assert not ACTIVE.search(report), ACTIVE.search(report).group(0)  # ... but cannot be a link or an image


def test_the_field_csv_defuses_formulas(hostile_pair):
    import csv, io

    rows = list(csv.reader(io.StringIO(field_csv(hostile_pair[2].analysis.comparison))))
    cells = [c for row in rows for c in row]
    assert any("HYPERLINK" in c for c in cells) and any("cmd|" in c for c in cells)
    assert [c for c in cells if c[:1] in "=+-@\t\r"] == []


def test_the_audit_csv_defuses_reviewer_and_file_names(hostile_pair):
    import csv, io

    from labelguard.ui.audit_page import audit_csv

    approved, revised, result = hostile_pair
    database.save_run(result.analysis, report=result.report)
    finding = result.report.flagged[0]
    database.set_review(result.analysis.approved.document.sha256, result.analysis.revised.document.sha256, finding.id,
                        finding.rule_id, finding.severity, "under_review", "", "=2+2")
    rows = list(csv.reader(io.StringIO(audit_csv(audit.list_events()))))
    cells = [c for row in rows for c in row]
    assert "'=2+2" in cells and [c for c in cells if c[:1] in "=+-@\t\r"] == []
    assert any("=SUM(A1).pdf" in c for c in cells)  # the file name is still there for the reader


def test_no_page_renders_label_text_as_markdown_links_or_images(hostile_pair, monkeypatch):
    approved, revised, _ = hostile_pair
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    at = AppTest.from_file(APP_PATH, default_timeout=90).run()
    open_page(at, "Upload & Analyze")
    at.file_uploader(key="uploader_approved_0").upload("=SUM(A1).pdf", approved, PDF).run()
    at.file_uploader(key="uploader_revised_0").upload("@evil.pdf", revised, PDF).run()
    at.button(key="start_analysis").click().run()
    assert not at.exception
    finding = at.session_state["verification"].report.flagged[0]
    at.text_input(key="reviewer_name").set_value("=2+2").run()
    at.button(key=f"start_{finding.id}").click().run()
    inspected = 0
    for page in ["Findings", "Results", "Review Queue", "AI Explanations", "Audit History", "Dashboard"]:
        open_page(at, page)
        if page == "AI Explanations":
            at.button(key="generate_explanation").click().run()
        assert not at.exception, page
        for kind in ("markdown", "caption", "info", "warning", "error", "success"):
            for element in getattr(at, kind):
                value = element.value or ""
                if value.startswith("<div"):  # the diff block: raw HTML built from html.escape()d text
                    assert "<script" not in value.lower() and "<img" not in value.lower()
                    continue
                inspected += 1
                assert not ACTIVE.search(value), (page, kind, ACTIVE.search(value).group(0), value[:120])
    assert inspected > 20  # the loop really looked at the pages


@pytest.fixture
def streamlit_without_windows_name_prefilter(monkeypatch):
    """Let a file name that contains a colon reach LabelGuard on every operating system.

    On Windows, Streamlit's uploader treats "name:rest" as an NTFS alternate data stream and keeps only the part before the
    first colon. A name such as "[x](https://evil.example/a).pdf" is therefore read as "[x](https", which has no extension, and
    Streamlit itself raises "Invalid file extension" before LabelGuard sees the file (an extra protection on Windows, and not
    something LabelGuard controls). These tests are about LabelGuard's OWN handling of a hostile name, so this fixture applies
    Streamlit's rule for other systems (the extension is whatever follows the last dot) to make the test identical everywhere.
    Nothing in LabelGuard is changed, and on Linux and macOS this is exactly what Streamlit already does.
    """
    import os

    from streamlit.elements.lib import file_uploader_utils

    monkeypatch.setattr(file_uploader_utils, "_get_main_filename_and_extension", lambda name: (name, os.path.splitext(name)[1]))


# Hostile Markdown in a file name. Streamlit accepts every one of these on every operating system as it is: a name with no colon,
# or a name whose part before the FIRST colon already ends in .pdf (which Windows reads as "file.pdf" plus a data-stream name).
NAMES_STREAMLIT_ACCEPTS_EVERYWHERE = [
    "[x](www.evil.example/a).pdf",
    "![x](www.evil.example/p.png).pdf",
    "evil.pdf:[x](https://evil.example/a).pdf",
]
# The original input of this test. Real Windows never lets it through to LabelGuard (see the fixture above).
ORIGINAL_NAME = "[x](https://evil.example/a).pdf"


def _error_for_hostile_name(name):
    at = open_page(AppTest.from_file(APP_PATH, default_timeout=90).run(), "Upload & Analyze")
    at.file_uploader(key="uploader_approved_0").upload(name, b"not a pdf", PDF).run()
    assert not at.exception
    assert at.error and not any(ACTIVE.search(e.value) for e in at.error)
    assert any("evil" in e.value for e in at.error), "the file name should still be visible to the reader"


@pytest.mark.parametrize("name", NAMES_STREAMLIT_ACCEPTS_EVERYWHERE)
def test_a_hostile_file_name_in_an_error_message_is_inert_with_streamlit_unchanged(name):
    """No patching at all: this is what really happens on this operating system."""
    _error_for_hostile_name(name)


@pytest.mark.parametrize("name", [ORIGINAL_NAME, *NAMES_STREAMLIT_ACCEPTS_EVERYWHERE])
def test_a_hostile_file_name_in_an_error_message_is_inert(name, streamlit_without_windows_name_prefilter):
    _error_for_hostile_name(name)


def _success_message_for_hostile_name(name, tmp_path):
    pdf = render_label_pdf(APPROVED, tmp_path / "a.pdf").read_bytes()
    at = open_page(AppTest.from_file(APP_PATH, default_timeout=90).run(), "Upload & Analyze")
    at.file_uploader(key="uploader_approved_0").upload(name, pdf, PDF).run()
    assert not at.exception
    shown = [e.value for kind in (at.success, at.error, at.info, at.warning, at.markdown, at.caption) for e in kind]
    mentioning = [v for v in shown if "evil" in v]
    assert mentioning, "the file name should be visible to the reader"
    assert not any(ACTIVE.search(v) for v in mentioning), mentioning


# Regression: the name used to be shown inside a Markdown code span, so a backtick in it ended the span and the rest became a
# live link. A VALID pdf is needed to reach this message (the error path is covered above).
BACKTICK_NAME = "a`[x](https://evil.example/p)`.pdf"
BACKTICK_NAME_WITHOUT_COLON = "a`[x](www.evil.example/p)`.pdf"


def test_a_hostile_file_name_with_a_backtick_cannot_break_out_of_the_success_message_with_streamlit_unchanged(tmp_path):
    _success_message_for_hostile_name(BACKTICK_NAME_WITHOUT_COLON, tmp_path)


@pytest.mark.parametrize("name", [BACKTICK_NAME, BACKTICK_NAME_WITHOUT_COLON])
def test_a_hostile_file_name_with_a_backtick_cannot_break_out_of_the_success_message(name, tmp_path, streamlit_without_windows_name_prefilter):
    _success_message_for_hostile_name(name, tmp_path)

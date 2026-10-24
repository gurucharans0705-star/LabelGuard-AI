"""Dashboard page: status overview and a summary of the latest analysis."""
from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

from labelguard.comparison.models import KIND_LABELS
from labelguard.db import database
from labelguard.pipeline import AnalysisResult
from labelguard.rules.models import SEVERITIES, SEVERITY_LABELS
from labelguard.ui import state
from labelguard.ui.style import page_header


def _file_status(loaded: state.LoadedFile | None) -> str:
    if loaded is None:
        return "Not loaded"
    return loaded.name if loaded.valid else "Invalid file"


def render_dashboard() -> None:
    page_header("LabelGuard AI", "Medical device label verification and change-impact review")

    approved, revised = state.get_file("approved"), state.get_file("revised")
    analysis = state.get_analysis()
    ocr = state.cached_ocr_status()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Approved label", _file_status(approved))
    c2.metric("Revised label", _file_status(revised))
    c3.metric("Analysis", "Complete" if analysis else "Not run")
    c4.metric("OCR engine", "Ready" if ocr.available else "Unavailable")
    if not ocr.available:
        st.caption(f"OCR: {ocr.message} Text-based PDFs work without it.")

    st.divider()
    if analysis is None:
        _quick_start()
    else:
        _summary(analysis)
        _verification_summary()
    _recent_runs()
    _limitations()


def _quick_start() -> None:
    st.subheader("Get started")
    st.markdown(
        "1. Open **Upload & Analyze** and load the approved label and the revised label (PDF).\n"
        "2. Check that both files pass validation.\n"
        "3. Press **Start label analysis**.\n"
        "4. Review the ranked findings and evidence under **Findings**, and the artwork under **Visual Comparison**."
    )
    st.button("Go to Upload & Analyze", type="primary", on_click=state.goto, args=("Upload & Analyze",), key="go_upload")


def _summary(analysis: AnalysisResult) -> None:
    comparison = analysis.comparison
    st.subheader("Latest comparison")
    a, b, c, d = st.columns(4)
    a.metric("Fields: exact match", comparison.matching_fields)
    b.metric("Fields: changed", comparison.changed_fields)
    c.metric("Fields: not detected in either", comparison.undetected_fields)
    d.metric("Text lines differing", len(comparison.text_changes))

    st.markdown("**Changed fields**")
    rows = []
    for item in comparison.fields:
        for change in item.changes:
            if change.kind == "unchanged":
                continue
            before = change.approved.value if change.approved else "not present"
            after = change.revised.value if change.revised else "not present"
            rows.append(f"- **{item.label}** ({KIND_LABELS[change.kind].lower()}): {before} → {after}")
    if rows:
        st.markdown("\n".join(rows[:8]))
        if len(rows) > 8:
            st.caption(f"... and {len(rows) - 8} more on the Results page.")
    else:
        st.write("No differences were found between the detected fields.")

    review = sum(1 for o in analysis.approved.observations + analysis.revised.observations if o.level == "review")
    if review:
        st.warning(f"{review} expected label element(s) were not detected. See Results → Extracted fields.")
    st.button("Open full results", type="primary", on_click=state.goto, args=("Results",), key="open_results_dash")


def _verification_summary() -> None:
    result = state.get_verification()
    if result is None:
        return
    report = result.report
    counts = report.severity_counts()
    st.subheader("Verification findings")
    cols = st.columns(len(SEVERITIES) + 1)
    cols[0].metric("Open", state.open_finding_count())
    for column, severity in zip(cols[1:], SEVERITIES):
        column.metric(SEVERITY_LABELS[severity], counts[severity])
    if report.not_evaluated:
        st.warning(f"{len(report.not_evaluated)} item(s) could not be verified and need a manual check.")
    st.button("Open findings", type="primary", on_click=state.goto, args=("Findings",), key="open_findings_dash")


def _recent_runs() -> None:
    """Show the last few analyses from the run log (file names, fingerprints and counts only)."""
    try:
        runs = database.list_runs(5)
    except (sqlite3.Error, OSError):
        st.caption("The run log could not be read, so recent analyses are not shown.")
        return
    if not runs:
        return
    st.subheader("Recent analyses")
    st.dataframe(
        pd.DataFrame([
            {
                "Run": run.id,
                "When (UTC)": run.created_at.replace("T", " ").replace("+00:00", ""),
                "Approved label": run.approved_filename,
                "Revised label": run.revised_filename,
                "Fields changed": run.fields_changed,
                "Text lines changed": run.text_modified + run.text_added + run.text_removed,
                "Findings flagged": run.findings_flagged,
                "Critical review": run.findings_critical,
                "OCR pages": run.ocr_pages,
            }
            for run in runs
        ]),
        hide_index=True,
        width="stretch",
    )
    st.caption("The run log stores file names, SHA-256 fingerprints and counts only. Label text is never saved.")


def _limitations() -> None:
    with st.expander("Scope and limitations"):
        st.markdown(
            "- LabelGuard AI is a **decision-support tool**. It does not certify regulatory compliance; "
            "a qualified person must review every finding.\n"
            "- Fields are found with rules and patterns, and can be missed or misread. Each detected value "
            "shows the label text it came from so you can verify it.\n"
            "- Differences are neutral observations. A difference is not automatically an error, because "
            "many changes are intended and approved.\n"
            "- OCR on scanned pages can misread characters; low-confidence pages are flagged.\n"
            "- Day 1 covers text and field comparison, a Markdown report and a simple run log. Artwork "
            "comparison, per-finding review history and PDF reports are not built yet."
        )

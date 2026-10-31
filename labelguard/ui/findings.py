"""Findings page: summary, filters, table, expandable details and review actions."""
from __future__ import annotations

import sqlite3
from collections import defaultdict

import pandas as pd
import streamlit as st

from labelguard.db import database
from labelguard.db.database import REVIEW_STATUS_LABELS, REVIEW_STATUSES
from labelguard.reporting.export import verification_to_markdown
from labelguard.ui.report_widget import render_report_download
from labelguard.rules.models import (
    RESULT_FLAGGED,
    SEVERITIES,
    SEVERITY_LABELS,
    SEVERITY_NOTE,
    Finding,
)
from labelguard.ui import state
from labelguard.ui.finding_view import render_finding_body
from labelguard.ui.review_widget import render_review_controls, render_reviewer_field
from labelguard.ui.style import page_header
from labelguard.verification import VerificationResult

MAX_DETAILS = 40  # expanders shown at once; the table always lists every match
_RESULT_LABELS = {RESULT_FLAGGED: "Discrepancy", "not_evaluated": "Not verified"}


def render_findings_page() -> None:
    page_header("Findings", "Discrepancies and unverified items, ranked by workflow priority")
    result = state.get_verification()
    if result is None:
        st.info("No analysis yet. Load the approved and the revised label and press **Start label analysis**.")
        st.button("Go to Upload & Analyze", type="primary", on_click=state.goto, args=("Upload & Analyze",), key="findings_go_upload")
        return

    if st.session_state.get("run_log_error"):
        st.warning(st.session_state["run_log_error"])
    error = st.session_state.get("review_error")
    if error and error.get("finding_id") is None:
        st.error(error["message"])

    _render_summary(result)
    st.caption(SEVERITY_NOTE)
    render_report_download(
        "Download verification report (Markdown)", lambda: verification_to_markdown(result, state.get_reviews()),
        kind="Verification report", filename="labelguard_verification_report.md", mime="text/markdown",
        key="download_verification_report",
    )
    render_reviewer_field()

    findings = result.report.findings
    if not findings:
        _render_empty(result)
        return

    matching, narrowed = _render_filters(findings)
    st.write(f"Showing **{len(matching)}** of {len(findings)} finding(s)" + (" (filters are narrowing the list)." if narrowed else "."))
    if narrowed:
        st.button("Reset filters", on_click=_reset_filters, key="reset_filters")
    if not matching:
        st.info("No finding matches the current filters. Press **Reset filters** to see every finding again.")
    else:
        st.dataframe(
            pd.DataFrame(_table_rows(matching)),
            hide_index=True,
            width="stretch",
            column_config={
                "Severity": st.column_config.TextColumn(width="medium"),
                "Rule": st.column_config.TextColumn(width="medium"),
                "Finding": st.column_config.TextColumn(width="large"),
                "Pages": st.column_config.TextColumn(width="small"),
                "Evidence": st.column_config.TextColumn(width="small"),
                "Review": st.column_config.TextColumn(width="small"),
            },
        )
        st.subheader("Finding details")
        history = _load_history(result)
        for finding in matching[:MAX_DETAILS]:
            _render_finding(finding, history.get(finding.id, []))
        if len(matching) > MAX_DETAILS:
            st.caption(f"Details are shown for the first {MAX_DETAILS} findings. Narrow the filters to see the others.")
    _render_outcomes(result)


# --- Summary --------------------------------------------------------------------------------------------
def _render_summary(result: VerificationResult) -> None:
    report = result.report
    counts = report.severity_counts()
    a, b, c = st.columns(3)
    a.metric("Discrepancies flagged", len(report.flagged))
    b.metric("Not verified (check manually)", len(report.not_evaluated))
    c.metric("Open (not yet resolved)", state.open_finding_count())
    for column, severity in zip(st.columns(len(SEVERITIES)), SEVERITIES):
        column.metric(SEVERITY_LABELS[severity], counts[severity])


def _render_empty(result: VerificationResult) -> None:
    outcomes = result.report.outcome_counts()
    st.success(
        f"No findings. {outcomes['passed']} rule(s) were evaluated and found no discrepancy between the two labels."
    )
    st.caption(
        "This is not a statement that the label is compliant. The rules only compare the two labels and check "
        "the items configured on the Rules page."
    )
    _render_outcomes(result)


# --- Filters and table ------------------------------------------------------------------------------------
FILTER_KEYS = ("filter_severity", "filter_category", "filter_status", "filter_result", "filter_text")


def _reset_filters() -> None:
    """Back to 'show everything'. Removing a widget's key makes it start from its default again."""
    for key in FILTER_KEYS:
        st.session_state.pop(key, None)


def _render_filters(findings: tuple[Finding, ...]) -> tuple[list[Finding], bool]:
    """The filter widgets. Returns the matching findings and whether any filter is narrowing the list."""
    categories = sorted({f.category_label for f in findings})
    # Two filters per row: with four across, the selected options are clipped and a reader cannot see what is chosen.
    (c1, c2), (c3, c4) = st.columns(2), st.columns(2)
    severities = c1.multiselect(
        "Severity", SEVERITIES, default=list(SEVERITIES), format_func=SEVERITY_LABELS.get, key="filter_severity"
    )
    chosen_categories = c2.multiselect("Category", categories, default=categories, key="filter_category")
    statuses = c3.multiselect(
        "Review status", REVIEW_STATUSES, default=list(REVIEW_STATUSES),
        format_func=REVIEW_STATUS_LABELS.get, key="filter_status",
    )
    kinds = c4.multiselect(
        "Result", list(_RESULT_LABELS), default=list(_RESULT_LABELS), format_func=_RESULT_LABELS.get, key="filter_result"
    )
    needle = st.text_input("Search the findings (title, values, evidence)", key="filter_text").strip().casefold()

    def matches(finding: Finding) -> bool:
        if finding.severity not in severities or finding.category_label not in chosen_categories:
            return False
        if state.review_status(finding.id) not in statuses or finding.result not in kinds:
            return False
        if not needle:
            return True
        haystack = " ".join(
            [finding.id, finding.title, finding.description, finding.approved_value or "", finding.revised_value or "", finding.rule_id]
            + [item.text for item in finding.evidence]
        ).casefold()
        return needle in haystack

    narrowed = bool(
        needle or set(severities) != set(SEVERITIES) or set(chosen_categories) != set(categories)
        or set(statuses) != set(REVIEW_STATUSES) or set(kinds) != set(_RESULT_LABELS)
    )
    return [finding for finding in findings if matches(finding)], narrowed


def _short(text: str | None, limit: int = 80) -> str:
    if not text:
        return "—"
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _table_rows(findings: list[Finding]) -> list[dict[str, str]]:
    return [
        {
            "Severity": f.severity_label,
            "Rule": f.rule_id,
            "Finding": f.title,
            "Category": f.category_label,
            "Original (approved)": _short(f.approved_value),
            "Revised": _short(f.revised_value),
            "Pages": ", ".join(map(str, f.pages)) or "—",
            "Evidence": "Complete" if f.evidence_complete else "Incomplete",
            "Review": REVIEW_STATUS_LABELS[state.review_status(f.id)],
        }
        for f in findings
    ]


# --- Details ------------------------------------------------------------------------------------------------
def _load_history(result: VerificationResult) -> dict[str, list[database.ReviewEvent]]:
    grouped: dict[str, list[database.ReviewEvent]] = defaultdict(list)
    try:
        events = database.review_history(
            result.analysis.approved.document.sha256, result.analysis.revised.document.sha256
        )
    except (sqlite3.Error, OSError):
        return {}
    for event in events:
        grouped[event.finding_id].append(event)
    return grouped


def _render_finding(finding: Finding, history: list[database.ReviewEvent]) -> None:
    # The title must not change with the review status: a changed title makes Streamlit rebuild the
    # expander, which would collapse it as soon as the reviewer changes the status.
    with st.expander(f"{finding.severity_label}  ·  {finding.rule_id}  ·  {finding.title}"):
        render_finding_body(finding)
        target = state.target_for(finding)
        if target is not None:
            render_review_controls(target, state.get_reviews().get(finding.id), history)


# --- Rule evaluation --------------------------------------------------------------------------------------------
def _render_outcomes(result: VerificationResult) -> None:
    counts = result.report.outcome_counts()
    with st.expander(
        f"How each rule was evaluated ({counts['passed']} passed, {counts['flagged']} flagged, "
        f"{counts['not_evaluated']} not evaluated, {counts['skipped']} skipped)"
    ):
        st.dataframe(
            pd.DataFrame([
                {
                    "Rule": o.rule_id,
                    "Name": o.rule_name,
                    "Outcome": {"passed": "Passed", "flagged": "Flagged", "not_evaluated": "Not evaluated",
                                "skipped": "Skipped"}.get(o.outcome, o.outcome),
                    "Findings": o.finding_count,
                    "Note": o.note,
                }
                for o in result.report.outcomes
            ]),
            hide_index=True,
            width="stretch",
        )
        st.caption(
            "\"Passed\" means the rule found no discrepancy between these two labels. \"Not evaluated\" means the "
            "check could not be performed, so a person must check it; it is never counted as a pass."
        )

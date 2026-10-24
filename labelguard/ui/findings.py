"""Findings page: summary, filters, table, expandable details and review actions."""
from __future__ import annotations

import sqlite3
from collections import defaultdict

import pandas as pd
import streamlit as st

from labelguard.db import database
from labelguard.db.database import REVIEW_STATUS_LABELS, REVIEW_STATUSES
from labelguard.reporting.export import verification_to_markdown
from labelguard.rules.models import (
    RESULT_FLAGGED,
    SEVERITIES,
    SEVERITY_LABELS,
    SEVERITY_NOTE,
    Evidence,
    Finding,
)
from labelguard.ui import state
from labelguard.ui.style import page_header
from labelguard.verification import VerificationResult

MAX_DETAILS = 40  # expanders shown at once; the table always lists every match
_SOURCE_LABELS = {"approved": "Approved label", "revised": "Revised label", "both": "Both labels"}
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
    if st.session_state.get("review_error"):
        st.error(st.session_state["review_error"])

    _render_summary(result)
    st.caption(SEVERITY_NOTE)
    st.download_button(
        "Download verification report (Markdown)",
        verification_to_markdown(result, state.get_reviews()),
        file_name="labelguard_verification_report.md",
        mime="text/markdown",
        key="download_verification_report",
    )

    findings = result.report.findings
    if not findings:
        _render_empty(result)
        return

    matching = _render_filters(findings)
    st.write(f"Showing **{len(matching)}** of {len(findings)} finding(s).")
    if not matching:
        st.info("No finding matches the current filters. Widen the filters above to see more.")
    else:
        st.dataframe(
            pd.DataFrame(_table_rows(matching)),
            hide_index=True,
            width="stretch",
            column_config={
                "Severity": st.column_config.TextColumn(width="small"),
                "Rule": st.column_config.TextColumn(width="small"),
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
def _render_filters(findings: tuple[Finding, ...]) -> list[Finding]:
    categories = sorted({f.category_label for f in findings})
    c1, c2, c3, c4 = st.columns(4)
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
            [finding.title, finding.description, finding.approved_value or "", finding.revised_value or "", finding.rule_id]
            + [item.text for item in finding.evidence]
        ).casefold()
        return needle in haystack

    return [finding for finding in findings if matches(finding)]


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


def _render_evidence(item: Evidence) -> None:
    source = _SOURCE_LABELS.get(item.source, item.source)
    where = f"page {item.page}" if item.page is not None else "page not recorded"
    if item.kind == "text":
        st.markdown(f"**{source} · {where}**")
        st.code(item.text or "(no text recorded)", language=None)
    elif item.kind == "absence":
        st.caption(f"{source}: {item.text}")
    else:
        kind = "image region" if item.kind == "region" else "measurement"
        st.caption(f"{source} · {where} · {kind}: {item.text}")


def _on_review(finding: Finding, status: str) -> None:
    state.review_finding(finding, status, st.session_state.get(f"note_{finding.id}", ""))


def _render_finding(finding: Finding, history: list[database.ReviewEvent]) -> None:
    status = state.review_status(finding.id)
    # The title must not change with the review status: a changed title makes Streamlit rebuild the
    # expander, which would collapse it as soon as the reviewer presses Acknowledge or Resolve.
    with st.expander(f"{finding.severity_label}  ·  {finding.rule_id}  ·  {finding.title}"):
        st.caption(f"Review status: **{REVIEW_STATUS_LABELS[status]}**")
        st.caption(
            f"{finding.category_label} · {'Discrepancy flagged' if finding.result == RESULT_FLAGGED else 'Could not be verified (not a pass)'}"
            f" · Finding ID {finding.id}"
        )
        st.write(finding.description)

        left, right = st.columns(2, gap="large")
        with left:
            st.markdown("**Original (approved label)**")
            st.code(finding.approved_value if finding.approved_value is not None else "—", language=None)
        with right:
            st.markdown("**Revised label**")
            st.code(finding.revised_value if finding.revised_value is not None else "—", language=None)

        st.markdown("**Evidence**")
        if finding.evidence:
            for item in finding.evidence:
                _render_evidence(item)
        if not finding.evidence_complete:
            st.warning(
                "The evidence for this finding is incomplete (no evidence was recorded, or an item has no page or "
                "content). Check the source label before relying on this finding."
            )
        if finding.recommended_action:
            st.info(f"**Recommended reviewer action:** {finding.recommended_action}")

        st.markdown("**Review**")
        record = state.get_reviews().get(finding.id)
        st.text_input(
            "Reviewer note (optional)", value=record.note if record else "", key=f"note_{finding.id}",
        )
        a, b, c = st.columns(3)
        a.button("Acknowledge", key=f"ack_{finding.id}", disabled=status == "acknowledged",
                 on_click=_on_review, args=(finding, "acknowledged"))
        b.button("Resolve", key=f"res_{finding.id}", type="primary", disabled=status == "resolved",
                 on_click=_on_review, args=(finding, "resolved"))
        c.button("Reopen", key=f"open_{finding.id}", disabled=status == "open",
                 on_click=_on_review, args=(finding, "open"))
        st.caption(
            "Acknowledging or resolving records a decision for this pair of files. It does not change the finding "
            "and is not an approval of the label."
        )
        if history:
            st.caption(
                "History: " + "; ".join(
                    f"{event.at.replace('T', ' ').replace('+00:00', ' UTC')}: {REVIEW_STATUS_LABELS[event.from_status]} → "
                    f"{REVIEW_STATUS_LABELS[event.to_status]}" + (f" ({event.note})" if event.note else "")
                    for event in history
                )
            )


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

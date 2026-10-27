"""Review Queue page: every discrepancy from the latest analysis of each pair of files, with its review status."""
from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

from labelguard.db import database
from labelguard.db.database import REVIEW_STATUS_LABELS, REVIEW_STATUSES, QueueItem
from labelguard.rules.models import SEVERITIES, SEVERITY_LABELS, SEVERITY_NOTE
from labelguard.ui import state
from labelguard.ui.finding_view import render_finding_body
from labelguard.ui.review_widget import render_review_controls, render_reviewer_field
from labelguard.ui.style import page_header

MAX_DETAILS = 25
_TO_DO = ("pending", "under_review", "escalated")


def render_queue_page() -> None:
    page_header("Review Queue", "Findings waiting for a person's decision, across all saved analyses")
    try:
        items = database.list_review_queue(include_not_verified=st.session_state.get("queue_unverified", False))
    except (sqlite3.Error, OSError):
        st.error("The review queue could not be read (the database may be read-only or locked).")
        return
    if state.get_verification() is None and not items:
        st.info("The queue is empty. Analyze two labels first; each discrepancy then appears here until a person decides.")
        st.button("Go to Upload & Analyze", type="primary", on_click=state.goto, args=("Upload & Analyze",), key="queue_go_upload")
        return

    render_reviewer_field()
    _render_counts(items)
    st.caption(SEVERITY_NOTE)

    c1, c2, c3 = st.columns(3)
    statuses = c1.multiselect("Status", REVIEW_STATUSES, default=list(_TO_DO), format_func=REVIEW_STATUS_LABELS.get, key="queue_status")
    severities = c2.multiselect("Priority", SEVERITIES, default=list(SEVERITIES), format_func=SEVERITY_LABELS.get, key="queue_severity")
    needle = c3.text_input("Search (finding, rule, file name)", key="queue_search").strip().casefold()
    d1, d2 = st.columns(2)
    d1.checkbox("Also list items that could not be verified", key="queue_unverified")
    show_demo = d2.checkbox("Include analyses of the fictional sample labels", value=True, key="queue_demo")

    shown = [i for i in items if i.status in statuses and i.finding.severity in severities and (show_demo or not i.is_demo)
             and (not needle or needle in " ".join([i.finding.title, i.finding.rule_id, i.approved_filename, i.revised_filename]).casefold())]
    resolved_hidden = sum(1 for i in items if i.status == "resolved") if "resolved" not in statuses else 0
    st.write(f"Showing **{len(shown)}** of {len(items)} finding(s)."
             + (f" {resolved_hidden} resolved finding(s) are hidden by the status filter; they are not deleted." if resolved_hidden else ""))
    if not shown:
        st.info("No finding matches the current filters.")
        return
    st.dataframe(pd.DataFrame([_row(i) for i in shown]), hide_index=True, width="stretch")

    st.subheader("Finding details")
    for item in shown[:MAX_DETAILS]:
        _render_item(item)
    if len(shown) > MAX_DETAILS:
        st.caption(f"Details are shown for the first {MAX_DETAILS} findings. Narrow the filters to see the others.")


def _render_counts(items: list[QueueItem]) -> None:
    counts = {status: sum(1 for i in items if i.status == status) for status in REVIEW_STATUSES}
    for column, status in zip(st.columns(len(REVIEW_STATUSES)), REVIEW_STATUSES):
        column.metric(REVIEW_STATUS_LABELS[status], counts[status])


def _row(item: QueueItem) -> dict[str, str]:
    return {
        "Priority": item.finding.severity_label, "Rule": item.finding.rule_id, "Finding": item.finding.title,
        "Labels compared": f"{item.approved_filename} → {item.revised_filename}",
        "Status": REVIEW_STATUS_LABELS[item.status], "Reviewer": item.reviewer or "—",
        "Updated (UTC)": (item.updated_at or "—").replace("T", " ").replace("+00:00", ""),
    }


def _render_item(item: QueueItem) -> None:
    finding = item.finding
    with st.expander(f"{finding.severity_label}  ·  {finding.rule_id}  ·  {finding.title}  ·  {REVIEW_STATUS_LABELS[item.status]}"):
        st.caption(f"{item.approved_filename} → {item.revised_filename} · analysis {item.job_uid} · {item.created_at.replace('T', ' ').replace('+00:00', ' UTC')}"
                   + (" · fictional sample" if item.is_demo else ""))
        render_finding_body(finding)
        target = state.ReviewTarget(finding.id, finding.rule_id, finding.severity, item.approved_sha256, item.revised_sha256)
        record = (database.ReviewRecord(finding.id, finding.rule_id, finding.severity, item.status, item.rationale,
                                        item.updated_at or "", item.reviewer) if item.updated_at else None)
        history = database.review_history(item.approved_sha256, item.revised_sha256, finding.id)
        render_review_controls(target, record, history, scope=f"q{item.job_id}_")

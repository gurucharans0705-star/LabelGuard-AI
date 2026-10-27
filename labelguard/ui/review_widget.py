"""Shared review controls: the reviewer's name, and the status buttons with a rationale.

Every page that lets a person change a status uses these, so the rules (a named reviewer, a rationale
for Resolve and Escalate, no automatic approval) live in one place.
"""
from __future__ import annotations

import streamlit as st

from labelguard.db import database
from labelguard.db.database import REVIEW_STATUS_LABELS, ReviewEvent, ReviewRecord
from labelguard.ui import state


def render_reviewer_field() -> None:
    """The name or initials that decisions are recorded under (kept for the session)."""
    st.text_input(
        "Reviewer (name or initials)", key="reviewer_name", max_chars=60,
        help="Review decisions are recorded under this name. Only a person can change a status; "
             "the AI explanation cannot approve or resolve anything.",
    )
    if not state.get_reviewer():
        st.caption("Enter your name or initials to enable the review buttons.")


def _on_click(target: state.ReviewTarget, status: str, scope: str) -> None:
    state.apply_review(target, status, st.session_state.get(f"note_{scope}{target.finding_id}", ""))


def render_review_controls(
    target: state.ReviewTarget, record: ReviewRecord | None, history: list[ReviewEvent], scope: str = ""
) -> None:
    """`scope` makes the widget keys unique when the same finding id can appear twice on one page."""
    status = record.status if record else "pending"
    st.markdown("**Review**")
    st.caption(
        f"Status: **{REVIEW_STATUS_LABELS[status]}**"
        + (f" · by {record.reviewer} · {record.updated_at.replace('T', ' ').replace('+00:00', ' UTC')}" if record and record.reviewer else "")
    )
    st.text_input(
        "Short rationale (required to resolve or escalate)", value=record.rationale if record else "",
        key=f"note_{scope}{target.finding_id}", max_chars=database.MAX_RATIONALE,
    )
    error = st.session_state.get("review_error")
    if error and error.get("finding_id") == target.finding_id:
        st.error(error["message"])
    named = bool(state.get_reviewer())
    a, b, c, d = st.columns(4)
    a.button("Start review", key=f"start_{scope}{target.finding_id}", disabled=not named or status == "under_review",
             on_click=_on_click, args=(target, "under_review", scope))
    b.button("Escalate", key=f"esc_{scope}{target.finding_id}", disabled=not named or status == "escalated",
             on_click=_on_click, args=(target, "escalated", scope))
    c.button("Resolve", key=f"res_{scope}{target.finding_id}", type="primary", disabled=not named or status == "resolved",
             on_click=_on_click, args=(target, "resolved", scope))
    d.button("Reopen", key=f"reopen_{scope}{target.finding_id}", disabled=not named or status == "pending",
             on_click=_on_click, args=(target, "pending", scope))
    st.caption(
        "A status records a person's decision about this finding for this pair of files. It does not change the "
        "finding, it is not an approval or release of the label, and nothing is ever deleted: every change stays in the history."
    )
    if history:
        st.caption("History: " + "; ".join(
            f"{event.at.replace('T', ' ').replace('+00:00', ' UTC')} · {event.reviewer or 'unknown'}: "
            f"{REVIEW_STATUS_LABELS.get(event.from_status, event.from_status)} → "
            f"{REVIEW_STATUS_LABELS.get(event.to_status, event.to_status)}"
            + (f" ({event.rationale})" if event.rationale else "")
            for event in history
        ))

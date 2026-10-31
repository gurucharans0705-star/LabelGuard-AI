"""Audit History page: the append-only record of analyses, decisions, reports and explanations."""
from __future__ import annotations

import json
import sqlite3

import pandas as pd
import streamlit as st

from labelguard.db import audit
from labelguard.safe_text import safe_csv
from labelguard.ui import state
from labelguard.ui.style import page_header


def audit_frame(events) -> pd.DataFrame:
    """One table row per audit event (what the page shows)."""
    return pd.DataFrame([{
        "When (UTC)": e.at.replace("T", " ").replace("+00:00", ""), "Event": e.type_label, "By": e.actor,
        "Summary": e.summary, "Record": e.entity_id, "Analysis": e.job_uid or "—",
    } for e in events])


def audit_csv(events) -> str:
    """The audit history as CSV text. File names and reviewer names are typed by people, so formula-like cells are defused."""
    return safe_csv(audit_frame(events))


def render_audit_page() -> None:
    page_header("Audit History", "What happened, when, and who did it")
    st.caption(
        "Events are added when something happens and are never edited or deleted by the application: a resolved "
        "finding stays here with its rationale. This guards against mistakes; it is not tamper-proofing, because "
        "someone with direct access to the database file could still change it."
    )
    c1, c2, c3 = st.columns([2, 2, 1])
    types = c1.multiselect("Event type", list(audit.EVENT_LABELS), default=list(audit.EVENT_LABELS),
                           format_func=audit.EVENT_LABELS.get, key="audit_types")
    needle = c2.text_input("Search (summary, person, record id)", key="audit_search").strip().casefold()
    limit = c3.selectbox("Show latest", [100, 500, 2000], key="audit_limit")
    show_demo = st.checkbox("Include events about the fictional sample data", value=True, key="audit_demo")
    try:
        events = audit.list_events(event_types=types or None, limit=limit) if types else []
    except (sqlite3.Error, OSError):
        st.error("The audit history could not be read (the database may be read-only or locked).")
        return
    events = [e for e in events if (show_demo or not e.is_demo)
              and (not needle or needle in " ".join([e.summary, e.actor, e.entity_id, e.job_uid or ""]).casefold())]
    if not events:
        st.info("No audit event matches. Events appear as soon as you analyze labels, change a review status, "
                "download a report or create an explanation.")
        return
    frame = audit_frame(events)
    st.write(f"**{len(events)}** event(s), newest first.")
    st.dataframe(frame, hide_index=True, width="stretch")
    csv = audit_csv(events)
    st.download_button("Download audit history (CSV)", csv, file_name="labelguard_audit_history.csv", mime="text/csv",
                       key="download_audit", on_click=state.log_report, args=("Audit history export", "labelguard_audit_history.csv", csv))
    with st.expander("Event details"):
        choice = st.selectbox("Event", range(len(events)), key="audit_event_pick",
                              format_func=lambda i: f"{events[i].at[:19].replace('T', ' ')} · {events[i].type_label} · {events[i].summary[:70]}")
        event = events[choice]
        st.json(json.loads(json.dumps(event.details, default=str)))
        st.caption(f"Event {event.event_uid} · {event.entity_type} {event.entity_id}")

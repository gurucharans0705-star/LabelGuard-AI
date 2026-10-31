"""LabelGuard AI - Streamlit entry point.

Run with:  streamlit run app.py
"""
from __future__ import annotations

import logging

import streamlit as st

from labelguard import ingestion
from labelguard.version import __version__
from labelguard.ui import state
from labelguard.ui.ai_page import render_ai_page
from labelguard.ui.audit_page import render_audit_page
from labelguard.ui.dashboard import render_dashboard
from labelguard.ui.findings import render_findings_page
from labelguard.ui.history_page import render_history_page
from labelguard.ui.impact_page import render_impact_page
from labelguard.ui.queue_page import render_queue_page
from labelguard.ui.results import render_results_page
from labelguard.ui.rules_page import render_rules_page
from labelguard.ui.style import apply_style, badge
from labelguard.ui.upload import render_upload_page
from labelguard.ui.visual import render_visual_page

logger = logging.getLogger("labelguard.app")

st.set_page_config(page_title="LabelGuard AI", page_icon="🛡️", layout="wide")


@st.cache_resource
def _startup_housekeeping() -> int:
    """Once per server start: remove upload folders that a killed process left in the temporary directory."""
    try:
        return ingestion.remove_stale_temp_folders()
    except Exception:  # housekeeping must never stop the app from starting
        logger.warning("Temporary-folder housekeeping failed")
        return 0


_startup_housekeeping()

DISCLAIMER = (
    "LabelGuard AI is a decision-support tool. Its findings are evidence-based flags for human review. "
    "They do not establish or certify regulatory compliance."
)


def render_sidebar() -> str:
    # A page can ask for navigation (e.g. after the analysis finishes). It must be applied
    # before the navigation widget is created.
    if "_goto" in st.session_state:
        st.session_state["nav"] = st.session_state.pop("_goto")
    st.session_state.setdefault("nav", state.PAGES[0])

    with st.sidebar:
        st.title("LabelGuard AI")
        st.caption(f"Medical device label verification · v{__version__}")
        page = st.radio("Navigation", state.PAGES, key="nav", label_visibility="collapsed")
        st.divider()
        approved, revised = state.get_file("approved"), state.get_file("revised")
        for title, loaded in (("Approved", approved), ("Revised", revised)):
            if loaded is None:
                chip = badge("not loaded", "info")
            elif loaded.valid:
                chip = badge("ready", "ok")
            else:
                chip = badge("invalid", "bad")
            st.markdown(f"{title} label &nbsp; {chip}", unsafe_allow_html=True)
        st.markdown(
            "Analysis &nbsp; " + (badge("complete", "ok") if state.get_analysis() else badge("not run", "info")),
            unsafe_allow_html=True,
        )
        if state.get_verification() is not None:
            open_count = state.open_finding_count()
            st.markdown(
                "Open findings &nbsp; " + (badge(str(open_count), "bad") if open_count else badge("0", "ok")),
                unsafe_allow_html=True,
            )
        st.divider()
        st.caption(DISCLAIMER)
    return page


PAGE_RENDERERS = {
    "Upload & Analyze": render_upload_page,
    "Findings": render_findings_page,
    "Visual Comparison": render_visual_page,
    "Results": render_results_page,
    "AI Explanations": render_ai_page,
    "Review Queue": render_queue_page,
    "Change Impact": render_impact_page,
    "Product History": render_history_page,
    "Audit History": render_audit_page,
    "Rules": render_rules_page,
}


def render_page(page: str) -> None:
    """Draw one page. An unexpected error shows a short message instead of a stack trace.

    The exception class is shown but not its text (which could contain file paths); the detail goes to the log.
    Streamlit's own control-flow exceptions (rerun, stop) are not `Exception`s, so they pass through untouched.
    """
    try:
        PAGE_RENDERERS.get(page, render_dashboard)()
    except Exception as exc:
        logger.exception("Unexpected error on the %s page", page)
        st.error(
            f"Something went wrong on the {page} page ({exc.__class__.__name__}). Your saved analyses, review "
            "decisions and audit history are not affected. Try again or open another page; if it keeps happening, "
            "check the application log."
        )


def main() -> None:
    apply_style()
    render_page(render_sidebar())


main()

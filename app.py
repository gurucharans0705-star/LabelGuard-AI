"""LabelGuard AI - Streamlit entry point.

Run with:  streamlit run app.py
"""
from __future__ import annotations

import streamlit as st

from labelguard import __version__
from labelguard.ui import state
from labelguard.ui.dashboard import render_dashboard
from labelguard.ui.results import render_results_page
from labelguard.ui.style import apply_style, badge
from labelguard.ui.upload import render_upload_page

st.set_page_config(page_title="LabelGuard AI", page_icon="🛡️", layout="wide")

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
        st.divider()
        st.caption(DISCLAIMER)
    return page


def main() -> None:
    apply_style()
    page = render_sidebar()
    if page == "Upload & Analyze":
        render_upload_page()
    elif page == "Results":
        render_results_page()
    else:
        render_dashboard()


main()

"""Upload & Analyze page: load two PDFs, see validation results, start the analysis."""
from __future__ import annotations

import logging

import streamlit as st

from labelguard.extraction.pdf_text import ExtractionError
from labelguard.pipeline import analyze_pair
from labelguard.samples.synthetic_labels import APPROVED_NAME, REVISED_NAME, ensure_sample_files
from labelguard.ui import state
from labelguard.ui.components import format_size
from labelguard.ui.style import page_header

logger = logging.getLogger(__name__)


def render_upload_page() -> None:
    page_header(
        "Upload & Analyze",
        "Load the approved label and the revised label as PDF files, then start the analysis.",
    )
    columns = st.columns(2, gap="large")
    for column, role in zip(columns, state.ROLES):
        with column:
            _render_slot(role)
    _render_samples()
    st.divider()
    _render_controls()


def _render_slot(role: str) -> None:
    title = state.ROLE_TITLES[role]
    st.subheader(title)
    uploaded = st.file_uploader(
        f"Upload the {title.lower()} (PDF)", type=["pdf"], key=state.uploader_key(role)
    )
    if uploaded is not None:
        token = (uploaded.name, uploaded.size)
        if st.session_state.get(f"token_{role}") != token:
            st.session_state[f"token_{role}"] = token
            state.set_file(role, state.load_file(role, uploaded.name, uploaded.getvalue(), "upload"))

    loaded = state.get_file(role)
    if loaded is None:
        st.info("No file loaded yet.")
        return
    if loaded.valid:
        origin = " · fictional sample" if loaded.source == "sample" else ""
        st.success(
            f"`{loaded.name}` passed validation · {loaded.page_count} page(s) · {format_size(len(loaded.data))}{origin}"
        )
    else:
        st.error(f"`{loaded.name}` cannot be used. {loaded.error}")
    if st.button("Remove file", key=f"remove_{role}"):
        state.clear_file(role)
        st.rerun()


def _render_samples() -> None:
    nothing_loaded = state.get_file("approved") is None and state.get_file("revised") is None
    with st.expander("No labels at hand? Use fictional sample labels", expanded=nothing_loaded):
        samples = ensure_sample_files()
        names = list(samples)
        left, right = st.columns(2)
        approved_choice = left.selectbox(
            "Approved label", names, index=names.index(APPROVED_NAME), key="sample_approved"
        )
        revised_choice = right.selectbox(
            "Revised label", names, index=names.index(REVISED_NAME), key="sample_revised"
        )
        if st.button("Load selected samples", key="load_samples"):
            for role, choice in (("approved", approved_choice), ("revised", revised_choice)):
                path = samples[choice]
                state.set_file(role, state.load_file(role, path.name, path.read_bytes(), "sample"))
                st.session_state.pop(f"token_{role}", None)
                state.reset_uploader(role)
            st.rerun()
        st.caption("All sample labels are fictional and were generated for testing. No real product data is used.")


def _render_controls() -> None:
    approved, revised = state.get_file("approved"), state.get_file("revised")
    ready = bool(approved and revised and approved.valid and revised.valid)
    ocr = state.cached_ocr_status()

    use_ocr = st.checkbox(
        "Use OCR for pages without selectable text (scans)",
        value=ocr.available,
        disabled=not ocr.available,
        key="use_ocr",
    )
    if not ocr.available:
        st.caption(f"OCR is unavailable: {ocr.message} Digital (text-based) PDFs still work.")
    if not ready:
        st.caption("Load two valid PDF files to enable the analysis.")

    if st.button("Start label analysis", type="primary", disabled=not ready, key="start_analysis"):
        _run_analysis(approved, revised, use_ocr)

    if state.get_analysis() is not None:
        st.success("An analysis is available for the loaded labels.")
        st.button("Open results", on_click=state.goto, args=("Results",), key="open_results_upload")


def _run_analysis(approved: state.LoadedFile, revised: state.LoadedFile, use_ocr: bool) -> None:
    with st.status("Analyzing labels...", expanded=True) as box:
        try:
            result = analyze_pair(
                (approved.data, approved.name),
                (revised.data, revised.name),
                use_ocr=use_ocr,
                progress=box.write,
            )
        except ExtractionError as exc:
            box.update(label="Analysis stopped", state="error")
            st.error(str(exc))
            return
        except Exception as exc:  # last-resort guard so the user sees a message, not a stack trace
            logger.exception("Unexpected error during analysis")
            box.update(label="Analysis failed", state="error")
            st.error(f"An unexpected error occurred ({exc.__class__.__name__}). Please try different files.")
            return
        box.update(label="Analysis complete", state="complete", expanded=False)
    state.store_analysis(result)
    state.goto("Results")
    st.rerun()

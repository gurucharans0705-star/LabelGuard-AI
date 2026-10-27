"""Upload & Analyze page: load two PDFs, see validation results, start the analysis."""
from __future__ import annotations

import logging

import streamlit as st

from labelguard.extraction.pdf_text import ExtractionError
from labelguard.rules.loader import RuleConfigError
from labelguard.samples.synthetic_labels import APPROVED_NAME, REVISED_NAME, ensure_sample_files
from labelguard.ui import state
from labelguard.ui.components import format_size
from labelguard.ui.style import page_header
from labelguard.verification import verify_pair

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

    tools = state.tool_status()
    run_visual = st.checkbox(
        "Compare artwork (visual comparison of the pages)",
        value=tools["vision"][0],
        disabled=not tools["vision"][0],
        key="run_visual",
    )
    if not tools["vision"][0]:
        st.caption(f"Visual comparison is unavailable: {tools['vision'][1]}")
    run_barcodes = st.checkbox(
        "Decode barcodes",
        value=tools["barcode"][0],
        disabled=not tools["barcode"][0],
        key="run_barcodes",
    )
    if not tools["barcode"][0]:
        st.caption(f"Barcode decoding is unavailable: {tools['barcode'][1]} Barcodes will be reported as not verified.")
    if not ready:
        st.caption("Load two valid PDF files to enable the analysis.")

    label_id = _label_link()
    if st.button("Start label analysis", type="primary", disabled=not ready, key="start_analysis"):
        is_demo = bool(approved and revised and approved.source == "sample" and revised.source == "sample")
        _run_analysis(approved, revised, use_ocr, run_visual, run_barcodes, is_demo=is_demo, label_id=label_id)

    if state.get_verification() is not None:
        st.success("An analysis is available for the loaded labels.")
        st.button("Open findings", on_click=state.goto, args=("Findings",), key="open_findings_upload")


def _label_link() -> str | None:
    """Optionally tie this analysis to a label record in the product catalog (shown only if a catalog exists)."""
    catalog = state.load_catalog()
    if catalog.is_empty:
        return None
    options = {"(not linked)": None} | {
        f"{l.label_code} v{l.version} · {catalog.market_name(l.market_code)} · {catalog.language_name(l.language_code)} "
        f"· {catalog.packaging_name(l.packaging_level_code)} ({l.label_id})": l.label_id
        for l in catalog.labels
    }
    choice = st.selectbox(
        "Link to a label record in the synthetic catalog (optional)", list(options), key="link_label",
        help="Linked analyses appear in Product History under that label.",
    )
    return options[choice]


def _run_analysis(
    approved: state.LoadedFile, revised: state.LoadedFile, use_ocr: bool, run_visual: bool, run_barcodes: bool,
    *, is_demo: bool = False, label_id: str | None = None,
) -> None:
    with st.status("Analyzing labels...", expanded=True) as box:
        try:
            result = verify_pair(
                (approved.data, approved.name),
                (revised.data, revised.name),
                use_ocr=use_ocr,
                run_visual=run_visual,
                run_barcodes=run_barcodes,
                progress=box.write,
            )
        except ExtractionError as exc:
            box.update(label="Analysis stopped", state="error")
            st.error(str(exc))
            return
        except RuleConfigError as exc:
            box.update(label="Analysis stopped: the rules file has a problem", state="error")
            st.error(str(exc))
            st.caption("Fix the rules file (see the Rules page) and start the analysis again.")
            return
        except Exception as exc:  # last-resort guard so the user sees a message, not a stack trace
            logger.exception("Unexpected error during analysis")
            box.update(label="Analysis failed", state="error")
            st.error(f"An unexpected error occurred ({exc.__class__.__name__}). Please try different files.")
            return
        box.update(label="Analysis complete", state="complete", expanded=False)
    state.store_verification(result, is_demo=is_demo, label_id=label_id)
    state.goto("Findings")
    st.rerun()

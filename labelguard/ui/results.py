"""Results page: differences, extracted fields with evidence, extracted text, document info."""
from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from labelguard.comparison.engine import inline_diff
from labelguard.comparison.models import KIND_LABELS, TextChange
from labelguard.fields.models import FIELD_LABELS, NOT_DETECTED
from labelguard.pipeline import AnalysisResult, LabelAnalysis
from labelguard.reporting.export import NOT_DETECTED_BOTH, comparison_to_markdown, field_rows
from labelguard.ui import state
from labelguard.ui.components import format_size, render_notes
from labelguard.ui.style import page_header

_RESULT_COLORS = {
    KIND_LABELS["unchanged"]: "#E3F1E7",
    KIND_LABELS["modified"]: "#FFF1D6",
    KIND_LABELS["added"]: "#E1EBF7",
    KIND_LABELS["removed"]: "#FDE3E3",
    NOT_DETECTED_BOTH: "#EDEFF2",
}
_METHOD_LABELS = {
    "labelled": "keyword match",
    "pattern": "format pattern",
    "heuristic": "inferred (please verify)",
    "warning section": "warning section",
}


def render_results_page() -> None:
    page_header("Results", "Differences and extracted fields, each backed by label text as evidence")
    analysis = state.get_analysis()
    if analysis is None:
        st.info("No analysis yet. Load two labels and press **Start label analysis**.")
        st.button("Go to Upload & Analyze", type="primary", on_click=state.goto, args=("Upload & Analyze",), key="results_go_upload")
        return

    render_notes(analysis.approved.ingestion.notes, "Approved label")
    render_notes(analysis.revised.ingestion.notes, "Revised label")

    st.download_button(
        "Download comparison report (Markdown)",
        comparison_to_markdown(analysis),
        file_name="labelguard_comparison_report.md",
        mime="text/markdown",
        key="download_report",
    )
    if st.session_state.get("run_id"):
        st.caption(f"This analysis was added to the run log as run #{st.session_state['run_id']}.")
    elif st.session_state.get("run_log_error"):
        st.warning(st.session_state["run_log_error"])

    tab_diff, tab_fields, tab_text, tab_info = st.tabs(
        ["Differences", "Extracted fields", "Extracted text", "Document info"]
    )
    with tab_diff:
        _render_differences(analysis)
    with tab_fields:
        _render_fields(analysis)
    with tab_text:
        _render_text(analysis)
    with tab_info:
        _render_info(analysis)


# --- Differences --------------------------------------------------------------
def _color_result(value: str) -> str:
    color = _RESULT_COLORS.get(value)
    return f"background-color: {color}" if color else ""


def _render_differences(analysis: AnalysisResult) -> None:
    comparison = analysis.comparison
    a, b, c = st.columns(3)
    a.metric("Fields: exact match", comparison.matching_fields)
    b.metric("Fields: changed", comparison.changed_fields)
    c.metric("Fields: not detected in either", comparison.undetected_fields)
    st.caption(
        "Differences are neutral observations, not findings of non-compliance. Many changes are intended and approved; "
        "a reviewer decides."
    )

    st.subheader("Field comparison")
    frame = pd.DataFrame(field_rows(comparison))
    styler = frame.style
    styler = (styler.map if hasattr(styler, "map") else styler.applymap)(_color_result, subset=["Result"])
    st.dataframe(styler, hide_index=True, width="stretch")
    st.download_button(
        "Download field comparison (CSV)",
        frame.to_csv(index=False),
        file_name="field_comparison.csv",
        mime="text/csv",
        key="download_field_csv",
    )

    st.subheader("Text differences")
    if any(doc.ocr_pages for doc in (analysis.approved.document, analysis.revised.document)):
        st.caption(
            "At least one label was read by OCR. OCR can join or split lines differently from the original PDF, "
            "so line-level text differences may reflect layout rather than wording. Rely on the field comparison first."
        )
    if analysis.comparison.text_diff and analysis.comparison.text_diff.word_level:
        st.caption("Because OCR was involved, text was compared word by word rather than line by line.")
    if comparison.text_identical:
        st.success(f"The text of both labels is identical ({comparison.unchanged_lines} lines compared).")
        return
    st.write(
        f"{comparison.unchanged_lines} line(s) identical · {comparison.count_text('modified')} modified · "
        f"{comparison.count_text('added')} added · {comparison.count_text('removed')} removed"
    )
    shown = st.multiselect(
        "Show", ["modified", "added", "removed"], default=["modified", "added", "removed"], key="diff_filter"
    )
    for change in comparison.text_changes:
        if change.kind in shown:
            st.markdown(_diff_html(change), unsafe_allow_html=True)


def _diff_html(change: TextChange) -> str:
    esc = html.escape
    page = change.approved_page or change.revised_page
    where = f' <span class="lg-muted">(page {page})</span>' if page else ""
    if change.kind == "added":
        body = f'<div class="lg-row"><b>Added in revised:</b> <span class="lg-ins">{esc(change.revised_text or "")}</span></div>'
    elif change.kind == "removed":
        body = f'<div class="lg-row"><b>Missing from revised:</b> <span class="lg-del">{esc(change.approved_text or "")}</span></div>'
    else:
        segments = inline_diff(change.approved_text or "", change.revised_text or "")
        old = " ".join(f'<span class="lg-del">{esc(t)}</span>' if k == "removed" else esc(t) for k, t in segments if k != "added")
        new = " ".join(f'<span class="lg-ins">{esc(t)}</span>' if k == "added" else esc(t) for k, t in segments if k != "removed")
        body = f'<div class="lg-row"><b>Approved:</b> {old}</div><div class="lg-row"><b>Revised:</b> {new}</div>'
    return f'<div class="lg-diff"><span class="lg-muted">{change.kind.capitalize()}</span>{where}{body}</div>'


# --- Extracted fields ----------------------------------------------------------
def evidence_rows(analysis: LabelAnalysis) -> list[dict[str, str]]:
    """One table row per detected value (or one 'Not detected' row per missing field)."""
    rows: list[dict[str, str]] = []
    for key, label in FIELD_LABELS.items():
        field = analysis.fields.get(key)
        if not field.detected:
            rows.append({"Field": label, "Value": NOT_DETECTED, "Evidence (label text)": "", "Page": "", "How found": ""})
            continue
        for hit in field.hits:
            rows.append({
                "Field": label,
                "Value": hit.value,
                "Evidence (label text)": hit.evidence,
                "Page": str(hit.page) if hit.page else "",
                "How found": _METHOD_LABELS.get(hit.method, hit.method),
            })
    return rows


def _render_fields(analysis: AnalysisResult) -> None:
    st.caption(
        "Every detected value shows the label text it was read from. Fields that could not be found are marked "
        "'Not detected'; nothing is guessed."
    )
    # Stacked at full width (not side by side) so the Evidence column stays readable.
    for title, label in (("Approved label", analysis.approved), ("Revised label", analysis.revised)):
        st.subheader(title)
        st.write(f"**Detected language(s):** {label.fields.language_summary}")
        st.dataframe(
            pd.DataFrame(evidence_rows(label)),
            hide_index=True,
            width="stretch",
            column_config={
                "Field": st.column_config.TextColumn(width="medium"),
                "Value": st.column_config.TextColumn(width="medium"),
                "Evidence (label text)": st.column_config.TextColumn(width="large"),
                "Page": st.column_config.TextColumn(width="small"),
                "How found": st.column_config.TextColumn(width="small"),
            },
        )
        review = [o for o in label.observations if o.level == "review"]
        info = [o for o in label.observations if o.level == "info"]
        with st.expander(f"Completeness observations ({len(review)} to review, {len(info)} informational)"):
            st.caption("Observations for a human reviewer. They are not compliance determinations.")
            for observation in review:
                st.warning(observation.message)
            for observation in info:
                st.caption(observation.message)
            if not label.observations:
                st.write("All expected elements were detected.")


# --- Extracted text ------------------------------------------------------------
def _render_text(analysis: AnalysisResult) -> None:
    for column, title, label in zip(
        st.columns(2, gap="large"), ("Approved label", "Revised label"), (analysis.approved, analysis.revised)
    ):
        with column:
            st.subheader(title)
            for page in label.document.pages:
                source = "read by OCR" if page.source == "ocr" else "selectable text"
                confidence = f" · mean confidence {page.ocr_confidence:.0f}%" if page.ocr_confidence is not None else ""
                st.caption(f"Page {page.page_number} · {source}{confidence}")
                st.code(page.text or "(no text could be read from this page)", language=None)
            st.download_button(
                "Download text (.txt)",
                label.document.full_text,
                file_name=f"{title.split()[0].lower()}_label_text.txt",
                key=f"download_text_{title.split()[0].lower()}",
            )


# --- Document info -------------------------------------------------------------
def _render_info(analysis: AnalysisResult) -> None:
    rows = []
    for title, label in (("Approved label", analysis.approved), ("Revised label", analysis.revised)):
        document = label.document
        rows.append({
            "Label": title,
            "File": document.filename,
            "Size": format_size(document.size_bytes),
            "Pages": str(document.page_count),
            "Pages read by OCR": ", ".join(map(str, document.ocr_pages)) or "none",
            "SHA-256": document.sha256,
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("The SHA-256 fingerprint identifies the exact file version, which matters for later audit trails.")

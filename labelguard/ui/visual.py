"""Visual Comparison page: old and new pages side by side, changed areas, and barcode decoding."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from labelguard.ui import state
from labelguard.ui.style import page_header
from labelguard.verification import VerificationResult
from labelguard.vision.barcode import BarcodeScan
from labelguard.vision.compare import (
    STATUS_DIFFERENCES,
    STATUS_FAILED,
    STATUS_IDENTICAL,
    STATUS_NOT_COMPARABLE,
    STATUS_TEXT_ONLY,
    PageVisual,
)

_STATUS_LABELS = {
    STATUS_IDENTICAL: "no visual difference",
    STATUS_TEXT_ONLY: "differs only where text changed",
    STATUS_DIFFERENCES: "differs outside changed text",
    STATUS_NOT_COMPARABLE: "cannot be compared reliably",
    STATUS_FAILED: "comparison failed",
}
_BARCODE_STATUS = {
    "decoded": "Decoded",
    "none_found": "No barcode decoded",
    "unavailable": "Decoder not installed",
    "failed": "Decoding failed",
}


def render_visual_page() -> None:
    page_header("Visual Comparison", "Old and new pages side by side, with changed areas highlighted")
    result = state.get_verification()
    if result is None:
        st.info("No analysis yet. Load the approved and the revised label and press **Start label analysis**.")
        st.button("Go to Upload & Analyze", type="primary", on_click=state.goto, args=("Upload & Analyze",), key="visual_go_upload")
        return

    st.caption(
        "A visual difference is something to look at, not a verdict. A moved logo, a new colour or a regenerated "
        "barcode may be intended. The comparison is also limited by how the pages render."
    )
    _render_pages(result)
    st.divider()
    _render_barcodes(result)


def _render_pages(result: VerificationResult) -> None:
    visual = result.visual
    if visual is None:
        st.info("Visual comparison was switched off for this analysis. Turn it on in Upload & Analyze to compare artwork.")
        return
    if visual.status != "ok":
        st.warning(f"{visual.message} The artwork was not compared; this is not a pass. Check it manually.")
        return
    if not visual.pages:
        st.warning("No pages could be paired between the two documents, so there is nothing to compare visually.")
    else:
        default = next((i for i, page in enumerate(visual.pages) if page.status == STATUS_DIFFERENCES), 0)
        index = st.selectbox(
            "Page pair",
            range(len(visual.pages)),
            index=default,
            format_func=lambda i: (
                f"Approved page {visual.pages[i].approved_page} ↔ revised page {visual.pages[i].revised_page}"
                f" · {_STATUS_LABELS.get(visual.pages[i].status, visual.pages[i].status)}"
            ),
            key="visual_page",
        )
        _render_page_pair(visual.pages[index])

    if visual.unpaired_approved:
        st.warning(
            f"Approved page(s) {', '.join(map(str, visual.unpaired_approved))} have no matching page in the revised "
            "label, so they were not compared visually."
        )
    if visual.unpaired_revised:
        st.warning(
            f"Revised page(s) {', '.join(map(str, visual.unpaired_revised))} have no matching page in the approved "
            "label, so they were not compared visually."
        )


def _render_page_pair(page: PageVisual) -> None:
    message = {
        STATUS_IDENTICAL: st.success,
        STATUS_TEXT_ONLY: st.info,
        STATUS_DIFFERENCES: st.warning,
        STATUS_NOT_COMPARABLE: st.warning,
        STATUS_FAILED: st.error,
    }.get(page.status, st.info)
    message(page.message)
    if page.reliability_note:
        st.warning(page.reliability_note)
    if page.alignment_note:
        st.caption(page.alignment_note)

    if page.approved_png and page.revised_png:
        titles = ["Approved label", "Revised label"]
        images = [page.approved_png, page.revised_png]
        if page.overlay_png:
            titles.append("Revised label, differences marked")
            images.append(page.overlay_png)
        for column, title, image in zip(st.columns(len(images), gap="small"), titles, images):
            with column:
                st.markdown(f"**{title}**")
                st.image(image, width="stretch")
        if page.overlay_png:
            st.caption(
                "Red boxes: areas that differ and are not explained by changed text. "
                "Amber boxes: areas that differ where text changed (reported as text findings)."
            )
    elif page.status == STATUS_FAILED:
        st.error("The page images could not be produced, so they cannot be shown.")

    if page.regions:
        rows = []
        for number, region in enumerate(page.regions, start=1):
            left, top, wide, tall = region.as_percent(page.width, page.height)
            rows.append({
                "Region": number,
                "Left (% of page)": left,
                "Top (% of page)": top,
                "Width (%)": wide,
                "Height (%)": tall,
                "Where text changed": "Yes" if region.explained else "No - not explained by text",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption(f"{page.difference_ratio:.1%} of this page's pixels differ.")


def _render_barcodes(result: VerificationResult) -> None:
    st.subheader("Barcode decoding")
    scans = (("Approved label", result.approved_barcodes), ("Revised label", result.revised_barcodes))
    if all(scan is None for _, scan in scans):
        st.info("Barcode decoding was switched off for this analysis.")
        return
    rows = [_barcode_row(title, scan) for title, scan in scans]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(
        "A barcode is reported as decoded only when the decoder returned a value. If nothing could be decoded, "
        "the barcode was not verified; that is not a pass. Decoding shows what a reader would see, not that the "
        "barcode meets print-quality requirements."
    )


def _barcode_row(title: str, scan: BarcodeScan | None) -> dict[str, str]:
    if scan is None:
        return {"Label": title, "Status": "Not run", "Decoded values": "—", "Detail": ""}
    values = "; ".join(f"{code.text} ({code.format}, page {code.page})" for code in scan.items) or "—"
    return {"Label": title, "Status": _BARCODE_STATUS.get(scan.status, scan.status), "Decoded values": values,
            "Detail": scan.message}

"""Shared rendering of one finding (used by the Findings page, the Review Queue and AI Explanations)."""
from __future__ import annotations

import streamlit as st

from labelguard.rules.models import RESULT_FLAGGED, Evidence, Finding
from labelguard.safe_text import escape_markdown as md

SOURCE_LABELS = {"approved": "Approved label", "revised": "Revised label", "both": "Both labels"}


def render_evidence(item: Evidence) -> None:
    source = SOURCE_LABELS.get(item.source, item.source)
    where = f"page {item.page}" if item.page is not None else "page not recorded"
    if item.kind == "text":
        st.markdown(f"**{source} · {where}**")
        st.code(item.text or "(no text recorded)", language=None)
    elif item.kind == "absence":
        st.caption(f"{source}: {md(item.text)}")
    else:
        kind = "image region" if item.kind == "region" else "measurement"
        st.caption(f"{source} · {where} · {kind}: {md(item.text)}")


def render_finding_body(finding: Finding) -> None:
    """Description, original vs revised values, evidence and the recommended action."""
    st.caption(
        f"{finding.category_label} · "
        f"{'Discrepancy flagged' if finding.result == RESULT_FLAGGED else 'Could not be verified (not a pass)'}"
        f" · Finding ID {finding.id}"
    )
    st.markdown(md(finding.description))
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("**Original (approved label)**")
        st.code(finding.approved_value if finding.approved_value is not None else "—", language=None)
    with right:
        st.markdown("**Revised label**")
        st.code(finding.revised_value if finding.revised_value is not None else "—", language=None)
    st.markdown("**Evidence**")
    for item in finding.evidence:
        render_evidence(item)
    if not finding.evidence_complete:
        st.warning(
            "The evidence for this finding is incomplete (no evidence was recorded, or an item has no page or "
            "content). Check the source label before relying on this finding."
        )
    if finding.recommended_action:
        st.info(f"**Recommended reviewer action:** {md(finding.recommended_action)}")

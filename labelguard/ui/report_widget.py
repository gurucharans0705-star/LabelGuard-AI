"""One download button for every report.

The report is built inside a guard: if building it fails, the page still renders (the findings, the
review controls and everything else stay usable) and the person sees a short message. The technical
detail goes to the application log only, because it could contain file paths. Every download that
does happen is written to the audit trail by the shared callback in `state.log_report`.
"""
from __future__ import annotations

import logging
from typing import Callable

import streamlit as st

from labelguard.ui import state

logger = logging.getLogger(__name__)


def render_report_download(
    label: str, build: Callable[[], str | bytes], *, kind: str, filename: str, mime: str, key: str,
) -> bool:
    """Show a download button for the report `build()` creates. Returns False if it could not be created."""
    try:
        content = build()
    except Exception:  # any failure while formatting must not take the page down
        logger.exception("Could not create the %s", kind.lower())
        st.error(
            f"The {kind.lower()} could not be created, so there is nothing to download right now. "
            "Your analysis, findings and review decisions are not affected. Try again, and if it keeps "
            "happening, check the application log."
        )
        return False
    st.download_button(
        label, content, file_name=filename, mime=mime, key=key,
        on_click=state.log_report, args=(kind, filename, content),
    )
    return True

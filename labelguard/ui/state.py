"""Session state helpers: what the user has loaded and the latest analysis result."""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

import streamlit as st

from labelguard.db import database
from labelguard.extraction.ocr import OcrStatus, get_ocr_status
from labelguard.extraction.pdf_text import ExtractionError
from labelguard.ingestion import validate_upload
from labelguard.pipeline import AnalysisResult

logger = logging.getLogger(__name__)

ROLES = ("approved", "revised")
ROLE_TITLES = {"approved": "Approved label", "revised": "Revised label"}
PAGES = ("Dashboard", "Upload & Analyze", "Results")


@dataclass(frozen=True)
class LoadedFile:
    """A label PDF the user has loaded, with the outcome of upfront validation."""

    role: str
    name: str
    data: bytes
    source: str  # "upload" or "sample"
    page_count: int | None = None
    error: str | None = None

    @property
    def valid(self) -> bool:
        return self.error is None


def load_file(role: str, name: str, data: bytes, source: str) -> LoadedFile:
    """Validate a file (type, size, format, encryption, page limit) and wrap the outcome."""
    try:
        return LoadedFile(role, name, data, source, page_count=validate_upload(name, data))
    except ExtractionError as exc:
        return LoadedFile(role, name, data, source, error=str(exc))


def get_file(role: str) -> LoadedFile | None:
    return st.session_state.get(f"file_{role}")


def set_file(role: str, loaded: LoadedFile) -> None:
    st.session_state[f"file_{role}"] = loaded
    st.session_state.pop("analysis", None)  # results from different files would be misleading


def clear_file(role: str) -> None:
    st.session_state.pop(f"file_{role}", None)
    st.session_state.pop(f"token_{role}", None)
    st.session_state.pop("analysis", None)
    reset_uploader(role)


def uploader_key(role: str) -> str:
    return f"uploader_{role}_{st.session_state.get(f'uploader_gen_{role}', 0)}"


def reset_uploader(role: str) -> None:
    """Give the file uploader a new key, which empties it."""
    st.session_state[f"uploader_gen_{role}"] = st.session_state.get(f"uploader_gen_{role}", 0) + 1


def get_analysis() -> AnalysisResult | None:
    return st.session_state.get("analysis")


def store_analysis(result: AnalysisResult) -> None:
    """Keep the latest analysis and add it to the run log.

    A problem with the log must never block the analysis, so a database error is
    remembered as a message to show instead of being raised.
    """
    st.session_state["analysis"] = result
    st.session_state.pop("run_id", None)
    st.session_state.pop("run_log_error", None)
    try:
        st.session_state["run_id"] = database.save_run(result)
    except (sqlite3.Error, OSError):
        logger.exception("Could not write to the run log")
        st.session_state["run_log_error"] = (
            "The analysis is complete, but it could not be added to the run log (the database file may be "
            "read-only or locked). Your results are not affected."
        )


def goto(page: str) -> None:
    """Request navigation to a page (applied at the start of the next run)."""
    st.session_state["_goto"] = page


@st.cache_data(ttl=60, show_spinner=False)
def cached_ocr_status() -> OcrStatus:
    """OCR availability, cached briefly because checking runs the Tesseract program."""
    return get_ocr_status()

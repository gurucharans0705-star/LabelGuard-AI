"""Session state helpers: what the user has loaded, the latest verification and review decisions."""
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
from labelguard.rules.models import Finding
from labelguard.verification import VerificationResult
from labelguard.vision.barcode import barcode_decoder_status
from labelguard.vision.images import vision_status

logger = logging.getLogger(__name__)

ROLES = ("approved", "revised")
ROLE_TITLES = {"approved": "Approved label", "revised": "Revised label"}
PAGES = ("Dashboard", "Upload & Analyze", "Findings", "Visual Comparison", "Results", "Rules")

# Everything that belongs to one analysis; cleared whenever a loaded file changes.
_ANALYSIS_KEYS = ("analysis", "verification", "reviews", "run_id", "run_log_error", "review_error")


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


def _clear_analysis() -> None:
    for key in _ANALYSIS_KEYS:  # results from different files would be misleading
        st.session_state.pop(key, None)


def set_file(role: str, loaded: LoadedFile) -> None:
    st.session_state[f"file_{role}"] = loaded
    _clear_analysis()


def clear_file(role: str) -> None:
    st.session_state.pop(f"file_{role}", None)
    st.session_state.pop(f"token_{role}", None)
    _clear_analysis()
    reset_uploader(role)


def uploader_key(role: str) -> str:
    return f"uploader_{role}_{st.session_state.get(f'uploader_gen_{role}', 0)}"


def reset_uploader(role: str) -> None:
    """Give the file uploader a new key, which empties it."""
    st.session_state[f"uploader_gen_{role}"] = st.session_state.get(f"uploader_gen_{role}", 0) + 1


# --- Results ----------------------------------------------------------------------------------------
def get_analysis() -> AnalysisResult | None:
    return st.session_state.get("analysis")


def get_verification() -> VerificationResult | None:
    return st.session_state.get("verification")


def get_reviews() -> dict[str, database.ReviewRecord]:
    return st.session_state.get("reviews", {})


def _pair_hashes(result: VerificationResult) -> tuple[str, str]:
    return result.analysis.approved.document.sha256, result.analysis.revised.document.sha256


def store_verification(result: VerificationResult) -> None:
    """Keep the latest verification, add it to the run log and load earlier review decisions.

    A problem with the database must never block the analysis, so a database error is
    remembered as a message to show instead of being raised.
    """
    _clear_analysis()
    st.session_state["analysis"] = result.analysis
    st.session_state["verification"] = result
    approved_hash, revised_hash = _pair_hashes(result)
    try:
        st.session_state["run_id"] = database.save_run(result.analysis, report=result.report)
        st.session_state["reviews"] = database.get_reviews(approved_hash, revised_hash)
    except (sqlite3.Error, OSError):
        logger.exception("Could not use the database")
        st.session_state["reviews"] = {}
        st.session_state["run_log_error"] = (
            "The analysis is complete, but the database could not be used (the file may be read-only or locked). "
            "Your results are not affected, but this run was not logged and review decisions cannot be saved."
        )


def review_finding(finding: Finding, status: str, note: str = "") -> None:
    """Save a reviewer's decision on a finding (used as a button callback)."""
    result = get_verification()
    if result is None:
        return
    approved_hash, revised_hash = _pair_hashes(result)
    st.session_state.pop("review_error", None)
    try:
        record = database.set_review(
            approved_hash, revised_hash, finding.id, finding.rule_id, finding.severity, status, note
        )
    except (sqlite3.Error, OSError, ValueError):
        logger.exception("Could not save the review decision")
        st.session_state["review_error"] = "The decision could not be saved (the database may be read-only or locked)."
        return
    reviews = dict(get_reviews())
    reviews[finding.id] = record
    st.session_state["reviews"] = reviews


def review_status(finding_id: str) -> str:
    record = get_reviews().get(finding_id)
    return record.status if record else "open"


def open_finding_count() -> int:
    """Flagged findings that have not been resolved yet."""
    result = get_verification()
    if result is None:
        return 0
    return sum(1 for finding in result.report.flagged if review_status(finding.id) != "resolved")


def goto(page: str) -> None:
    """Request navigation to a page (applied at the start of the next run)."""
    st.session_state["_goto"] = page


@st.cache_data(ttl=60, show_spinner=False)
def cached_ocr_status() -> OcrStatus:
    """OCR availability, cached briefly because checking runs the Tesseract program."""
    return get_ocr_status()


def tool_status() -> dict[str, tuple[bool, str]]:
    """Availability of the optional tools: OCR, OpenCV and the barcode decoder."""
    ocr = cached_ocr_status()
    return {
        "ocr": (ocr.available, ocr.message),
        "vision": vision_status(),
        "barcode": barcode_decoder_status(),
    }

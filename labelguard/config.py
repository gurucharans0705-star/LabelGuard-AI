"""Central configuration for LabelGuard AI.

Anything that can differ between machines (paths, limits, OCR settings) lives
here, so the rest of the code never hard-codes it. No secrets are stored in code.
Most values can be overridden with environment variables (see .env.example).
"""
from __future__ import annotations

import os
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    """Read an integer environment variable, falling back to `default` if unset or invalid."""
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


# --- Paths -----------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SAMPLES_DIR = DATA_DIR / "samples"
# SQLite database file (analysis-run log). Override with LABELGUARD_DB_PATH.
DB_PATH = Path(os.environ.get("LABELGUARD_DB_PATH", "").strip() or DATA_DIR / "labelguard.db")

# --- Upload limits ---------------------------------------------------------
MAX_UPLOAD_MB = _env_int("LABELGUARD_MAX_UPLOAD_MB", 25)
MAX_PAGES = _env_int("LABELGUARD_MAX_PAGES", 20)

# --- Text extraction and OCR -----------------------------------------------
# A page with fewer extractable characters than this is treated as having
# "insufficient text" (a scan, or text converted to outlines) and gets OCR.
OCR_MIN_CHARS = _env_int("LABELGUARD_OCR_MIN_CHARS", 25)
OCR_ENABLED = _env_bool("LABELGUARD_OCR_ENABLED", True)
OCR_DPI = _env_int("LABELGUARD_OCR_DPI", 300)
OCR_MAX_PIXELS = 4000  # longest side of the rendered page image, to bound memory use
OCR_LANGUAGE = os.environ.get("LABELGUARD_OCR_LANG", "eng").strip() or "eng"
# Mean OCR confidence (0-100) below which results are flagged for manual checking.
OCR_LOW_CONFIDENCE = 60
# Optional full path to tesseract.exe if it is not on PATH (Windows).
TESSERACT_CMD = os.environ.get("TESSERACT_CMD", "").strip() or None

"""Optional OCR fallback using the Tesseract program (via pytesseract and Pillow).

OCR is never required. If Tesseract is missing, disabled or fails, functions in
this module report that in their return value instead of raising, so the rest of
the application keeps working and can tell the user exactly what is missing.
"""
from __future__ import annotations

import io
import os
import shutil
import statistics
from dataclasses import dataclass
from pathlib import Path

from labelguard import config
from labelguard.extraction.normalize import normalize_line, normalize_text
from labelguard.extraction.pdf_render import render_page_png
from labelguard.extraction.pdf_text import TextLine

INSTALL_HINT = (
    "Install Tesseract (Windows: the UB Mannheim installer) and restart the app, "
    "or set the TESSERACT_CMD environment variable to the full path of tesseract.exe."
)


@dataclass(frozen=True)
class OcrStatus:
    available: bool
    message: str
    version: str | None = None
    command: str | None = None


@dataclass(frozen=True)
class OcrPageResult:
    text: str
    lines: tuple[TextLine, ...]
    confidence: float | None  # mean word confidence 0-100
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.text.strip())


def find_tesseract() -> str | None:
    """Locate the Tesseract executable: TESSERACT_CMD, then PATH, then usual Windows folders."""
    if config.TESSERACT_CMD and Path(config.TESSERACT_CMD).is_file():
        return config.TESSERACT_CMD
    on_path = shutil.which("tesseract")
    if on_path:
        return on_path
    candidates: list[Path] = []
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(variable)
        if root:
            candidates.append(Path(root) / "Tesseract-OCR" / "tesseract.exe")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Programs" / "Tesseract-OCR" / "tesseract.exe")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def get_ocr_status() -> OcrStatus:
    """Report whether OCR can run right now, and if not, why."""
    if not config.OCR_ENABLED:
        return OcrStatus(False, "OCR is switched off (LABELGUARD_OCR_ENABLED is false).")
    try:
        import pytesseract
        from PIL import Image  # noqa: F401  (checks that Pillow is installed)
    except ImportError as exc:
        return OcrStatus(False, f"OCR Python packages are missing ({exc.name}). Run: pip install pytesseract pillow")

    command = find_tesseract()
    if command is None:
        return OcrStatus(False, f"The Tesseract program was not found. {INSTALL_HINT}")
    pytesseract.pytesseract.tesseract_cmd = command
    try:
        version = str(pytesseract.get_tesseract_version())
        languages = pytesseract.get_languages(config="")
    except Exception as exc:  # TesseractNotFoundError, permission problems, broken install
        return OcrStatus(False, f"Tesseract was found but could not be run ({exc.__class__.__name__}). {INSTALL_HINT}", command=command)
    if config.OCR_LANGUAGE not in languages:
        return OcrStatus(
            False,
            f"Tesseract has no '{config.OCR_LANGUAGE}' language data installed (available: {', '.join(languages) or 'none'}).",
            version=version,
            command=command,
        )
    return OcrStatus(True, f"Tesseract {version} is ready.", version=version, command=command)


def _oriented_zoom(width: float, height: float) -> float:
    """Render zoom giving about OCR_DPI, capped so the image stays a manageable size."""
    longest = max(width, height, 1.0)
    return max(0.5, min(config.OCR_DPI / 72.0, config.OCR_MAX_PIXELS / longest))


def ocr_page(pdf_bytes: bytes, page_number: int, width: float, height: float) -> OcrPageResult:
    """Run OCR on one PDF page. Never raises; failures are returned in `error`."""
    status = get_ocr_status()
    if not status.available:
        return OcrPageResult("", (), None, error=status.message)

    import pytesseract
    from PIL import Image

    zoom = _oriented_zoom(width, height)
    try:
        png = render_page_png(pdf_bytes, page_number, zoom=zoom)
        with Image.open(io.BytesIO(png)) as image:
            data = pytesseract.image_to_data(
                image, lang=config.OCR_LANGUAGE, output_type=pytesseract.Output.DICT, timeout=90
            )
    except Exception as exc:  # TesseractError, timeout (RuntimeError), image problems
        return OcrPageResult("", (), None, error=f"OCR failed on page {page_number}: {exc.__class__.__name__}: {exc}")

    grouped: dict[tuple[int, int, int], list[tuple[str, float, int, int, int, int]]] = {}
    for index, raw_text in enumerate(data["text"]):
        word = (raw_text or "").strip()
        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            continue
        if not word or confidence < 0:
            continue
        key = (data["block_num"][index], data["par_num"][index], data["line_num"][index])
        grouped.setdefault(key, []).append(
            (word, confidence, data["left"][index], data["top"][index], data["width"][index], data["height"][index])
        )

    lines: list[TextLine] = []
    confidences: list[float] = []
    for words in grouped.values():
        text = normalize_line(" ".join(word[0] for word in words))
        if not text:
            continue
        confidences.extend(word[1] for word in words)
        x0 = min(word[2] for word in words) / zoom
        y0 = min(word[3] for word in words) / zoom
        x1 = max(word[2] + word[4] for word in words) / zoom
        y1 = max(word[3] + word[5] for word in words) / zoom
        size = statistics.median(word[5] for word in words) / zoom
        lines.append(TextLine(page_number, (round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)), text, round(size, 1)))

    mean_confidence = round(sum(confidences) / len(confidences), 1) if confidences else None
    text = normalize_text("\n".join(line.text for line in lines))
    return OcrPageResult(text, tuple(lines), mean_confidence)

"""Render PDF pages to PNG images (for on-screen preview now, OCR and artwork comparison later)."""
from __future__ import annotations

import pymupdf


def render_page_png(source: bytes, page_number: int = 1, zoom: float = 2.0) -> bytes:
    """Return page `page_number` (1-based) of the PDF as PNG bytes."""
    with pymupdf.open(stream=source, filetype="pdf") as doc:
        if not 1 <= page_number <= doc.page_count:
            raise ValueError(f"Page {page_number} is out of range (1-{doc.page_count}).")
        pixmap = doc[page_number - 1].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        return pixmap.tobytes("png")

"""Optional barcode decoding for rendered PDF pages (uses the zxing-cpp package).

A barcode is only reported as decoded when the decoder actually returned a value. If the
package is missing, or nothing could be read, the scan says so, and no other part of the
application treats the barcode as verified.
"""
from __future__ import annotations

from dataclasses import dataclass

import pymupdf

INSTALL_HINT = "Install it with: pip install zxing-cpp"

STATUS_DECODED = "decoded"
STATUS_NONE_FOUND = "none_found"
STATUS_UNAVAILABLE = "unavailable"
STATUS_FAILED = "failed"


@dataclass(frozen=True)
class DecodedBarcode:
    text: str
    format: str
    page: int  # 1-based
    bbox: tuple[float, float, float, float] | None = None  # PDF points

    @property
    def digits(self) -> str:
        """Only the digits of the decoded value, for comparing with printed identifiers."""
        return "".join(char for char in self.text if char.isdigit())


@dataclass(frozen=True)
class BarcodeScan:
    status: str
    message: str
    items: tuple[DecodedBarcode, ...] = ()

    @property
    def decoded(self) -> bool:
        return self.status == STATUS_DECODED and bool(self.items)


def _load_decoder():
    """The zxingcpp module, or None if it is not installed."""
    try:
        import zxingcpp
    except ImportError:
        return None
    return zxingcpp


def barcode_decoder_status() -> tuple[bool, str]:
    if _load_decoder() is None:
        return False, f"The barcode decoder (zxing-cpp) is not installed. {INSTALL_HINT}"
    return True, "Barcode decoder is ready."


def scan_pdf_barcodes(pdf_bytes: bytes, zoom: float = 3.0) -> BarcodeScan:
    """Try to decode barcodes on every page. Never raises; problems are reported in the result."""
    decoder = _load_decoder()
    if decoder is None:
        return BarcodeScan(
            STATUS_UNAVAILABLE,
            f"The barcode decoder (zxing-cpp) is not installed, so barcodes were not read. {INSTALL_HINT}",
        )
    try:
        import numpy as np

        found: list[DecodedBarcode] = []
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            for index in range(doc.page_count):
                pixmap = doc[index].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
                image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
                for result in decoder.read_barcodes(image):
                    if not result.valid or not result.text:
                        continue
                    corners = [result.position.top_left, result.position.top_right,
                               result.position.bottom_right, result.position.bottom_left]
                    xs, ys = [c.x / zoom for c in corners], [c.y / zoom for c in corners]
                    found.append(DecodedBarcode(
                        result.text, str(result.format), index + 1,
                        (round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)),
                    ))
    except Exception as exc:  # decoder or rendering problems must not stop an analysis
        return BarcodeScan(STATUS_FAILED, f"Barcode decoding failed ({exc.__class__.__name__}: {exc}).")
    if not found:
        return BarcodeScan(STATUS_NONE_FOUND, "No barcode could be decoded on any page.")
    return BarcodeScan(STATUS_DECODED, f"{len(found)} barcode(s) decoded.", tuple(found))

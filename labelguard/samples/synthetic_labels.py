"""Synthetic (fictional) medical-device labels for development, demos and tests.

Nothing here refers to a real product, company or regulatory submission.
The "revised" label deliberately differs from the "approved" one in known ways
(see EXPECTED_V1_V2_DIFFERENCES), so later comparison and validation features
can be tested against a documented ground truth.

Run `python -m labelguard.samples.synthetic_labels` to write the PDFs into
data/samples/.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass, replace
from pathlib import Path

import pymupdf
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from labelguard import config

APPROVED_NAME = "Approved label (v1)"
REVISED_NAME = "Revised label (v2)"
INCOMPLETE_NAME = "Incomplete label (lot, dates, manufacturer missing)"
SCANNED_NAME = "Scanned copy of approved label (image only)"


def gtin14_check_digit(body13: str) -> int:
    """GS1 check digit for the first 13 digits of a GTIN-14."""
    if len(body13) != 13 or not body13.isdigit():
        raise ValueError("GTIN-14 body must be exactly 13 digits")
    total = 0
    for position, char in enumerate(reversed(body13)):
        total += int(char) * (3 if position % 2 == 0 else 1)
    return (10 - total % 10) % 10


def build_udi_di(body13: str) -> str:
    """Return a UDI-DI string such as (01)08901234567895."""
    return f"(01){body13}{gtin14_check_digit(body13)}"


@dataclass(frozen=True)
class LabelSpec:
    product_name: str
    ref: str
    udi_di_body: str
    lot: str | None
    mfg_date: str | None
    expiry: str | None
    sterility: str | None
    storage: str | None
    warnings: tuple[str, ...]
    manufacturer: str | None = "Northwind Medical Devices Ltd."
    address: str = "12 Example Road, Sampletown, Exampleland"
    band_color: str = "#1F4E79"
    logo_x_mm: float = 128.0

    @property
    def udi_di(self) -> str:
        return build_udi_di(self.udi_di_body)


APPROVED = LabelSpec(
    product_name="NW-IV200 Gravity Infusion Set (20 drops/mL)",
    ref="NW-IV200-20",
    udi_di_body="0890123456789",
    lot="A24-0917",
    mfg_date="2024-07-01",
    expiry="2029-06-30",
    sterility="STERILE EO",
    storage="Store between 15 and 30 degrees C. Keep dry.",
    warnings=(
        "Single use only. Do not reuse.",
        "Do not use if package is damaged.",
        "Contains no natural rubber latex.",
        "Caution: Rx only. Federal law restricts this device to sale by or on "
        "the order of a physician.",
    ),
)

REVISED = LabelSpec(
    product_name="NW-IV200 Gravity Infusion Set (15 drops/mL)",
    ref="NW-IV200-15",
    udi_di_body="0890123456802",
    lot="A24-0917",
    mfg_date="2024-07-01",
    expiry="2029-06-30",
    sterility="STERILE EO",
    storage="Store between 5 and 40 degrees C. Keep dry.",
    warnings=(
        "Single use only. Do not reuse.",
        "Contains natural rubber latex.",
        "Caution: Rx only. Federal law restricts this device to sale by or on "
        "the order of a physician.",
    ),
    band_color="#2B5C8A",
    logo_x_mm=121.0,
)

# A label with several expected elements deliberately left out (for missing-field tests).
INCOMPLETE = replace(APPROVED, lot=None, mfg_date=None, expiry=None, manufacturer=None)

# Ground truth for tests and demos: what changed between APPROVED and REVISED.
EXPECTED_V1_V2_DIFFERENCES = (
    "Product name: drop factor 20 drops/mL -> 15 drops/mL",
    "REF changed: NW-IV200-20 -> NW-IV200-15",
    "UDI-DI changed (new GTIN)",
    "Warning removed: 'Do not use if package is damaged.'",
    "Latex statement changed: 'no natural rubber latex' -> 'natural rubber latex'",
    "Storage temperature range widened: 15-30 C -> 5-40 C",
    "Artwork: header colour changed and logo moved 7 mm left",
)


def _draw_barcode(c: canvas.Canvas, digits: str, x: float, y: float, height: float) -> None:
    """Draw a fake (non-scannable) barcode so artwork comparison has something to see."""
    c.setFillColor(black)
    cursor = x
    for char in digits:
        bar = (int(char) % 3 + 1) * 0.5 * mm
        c.rect(cursor, y, bar, height, fill=1, stroke=0)
        cursor += bar + 0.7 * mm


def render_label_pdf(spec: LabelSpec, path: Path) -> Path:
    """Write a one-page synthetic label PDF with selectable text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = 160 * mm, 130 * mm
    c = canvas.Canvas(str(path), pagesize=(width, height))
    c.setTitle(f"Synthetic label {spec.ref}")
    c.setAuthor("LabelGuard AI synthetic sample")

    # Border and header band
    c.setStrokeColor(black)
    c.setLineWidth(1)
    c.rect(5 * mm, 5 * mm, width - 10 * mm, height - 10 * mm)
    c.setFillColor(HexColor(spec.band_color))
    c.rect(5 * mm, height - 25 * mm, width - 10 * mm, 20 * mm, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 13)
    if spec.manufacturer:
        c.drawString(10 * mm, height - 17 * mm, spec.manufacturer)
    c.rect(spec.logo_x_mm * mm, height - 22 * mm, 14 * mm, 14 * mm, fill=1, stroke=0)

    # Identification block
    c.setFillColor(black)
    y = height - 33 * mm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(10 * mm, y, spec.product_name)

    c.setFont("Helvetica", 9)
    y -= 7 * mm
    c.drawString(10 * mm, y, f"REF: {spec.ref}")
    if spec.sterility:
        c.drawString(85 * mm, y, spec.sterility)
    y -= 5.5 * mm
    if spec.lot:
        c.drawString(10 * mm, y, f"LOT: {spec.lot}")
    if spec.expiry:
        c.drawString(85 * mm, y, f"EXP: {spec.expiry}")
    y -= 5.5 * mm
    if spec.mfg_date:
        c.drawString(10 * mm, y, f"MFG: {spec.mfg_date}")
    y -= 6.5 * mm
    c.drawString(10 * mm, y, f"UDI-DI: {spec.udi_di}")
    y -= 10 * mm
    _draw_barcode(c, spec.udi_di_body + str(gtin14_check_digit(spec.udi_di_body)), 10 * mm, y, 8 * mm)

    # Warnings
    y -= 8 * mm
    c.setFont("Helvetica-Bold", 9)
    c.drawString(10 * mm, y, "WARNINGS")
    c.setFont("Helvetica", 8.5)
    for warning in spec.warnings:
        for index, line in enumerate(textwrap.wrap(warning, width=78)):
            y -= 4.2 * mm
            prefix = "- " if index == 0 else "  "
            c.drawString(10 * mm, y, prefix + line)

    # Storage and manufacturer address
    y -= 7 * mm
    if spec.storage:
        c.drawString(10 * mm, y, spec.storage)
    if spec.manufacturer:
        c.setFont("Helvetica", 8)
        c.drawString(10 * mm, 9 * mm, f"Manufacturer: {spec.manufacturer}, {spec.address}")

    c.showPage()
    c.save()
    return path


def make_scanned_copy(pdf_bytes: bytes, dpi: int = 200) -> bytes:
    """Rasterize every page into an image-only PDF (no selectable text), simulating a scan."""
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as source, pymupdf.open() as scanned:
        for page in source:
            png = page.get_pixmap(dpi=dpi, alpha=False).tobytes("png")
            new_page = scanned.new_page(width=page.rect.width, height=page.rect.height)
            new_page.insert_image(new_page.rect, stream=png)
        return scanned.tobytes()


def ensure_sample_files(directory: Path | None = None) -> dict[str, Path]:
    """Create the sample PDFs if missing and return {display name: path}."""
    folder = directory or config.SAMPLES_DIR
    files = {
        APPROVED_NAME: folder / "approved_label_v1.pdf",
        REVISED_NAME: folder / "revised_label_v2.pdf",
        INCOMPLETE_NAME: folder / "incomplete_label.pdf",
        SCANNED_NAME: folder / "approved_label_scanned.pdf",
    }
    specs = {APPROVED_NAME: APPROVED, REVISED_NAME: REVISED, INCOMPLETE_NAME: INCOMPLETE}
    for name, spec in specs.items():
        if not files[name].exists():
            render_label_pdf(spec, files[name])
    if not files[SCANNED_NAME].exists():
        files[SCANNED_NAME].write_bytes(make_scanned_copy(files[APPROVED_NAME].read_bytes()))
    return files


if __name__ == "__main__":
    for name, path in ensure_sample_files().items():
        print(f"{name}: {path}")

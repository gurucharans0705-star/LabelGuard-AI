import io

import pytest

from labelguard.extraction.pdf_render import render_page_png
from labelguard.samples.synthetic_labels import render_label_pdf, APPROVED
from labelguard.vision import barcode
from labelguard.vision.compare import align_pages, compare_page_images
from labelguard.vision.images import vision_status

A4 = (595.0, 842.0)


@pytest.fixture(scope="module")
def page_png(approved_pdf):
    return render_page_png(approved_pdf.read_bytes(), 1, zoom=1.5)


def test_vision_is_available_here():
    assert vision_status()[0]


def test_identical_pages_report_no_difference(page_png):
    result = compare_page_images(page_png, page_png, approved_size=A4, revised_size=A4)
    assert result.status == "identical" and not result.regions


def test_garbage_bytes_fail_with_a_message_instead_of_raising(page_png):
    result = compare_page_images(b"not a png", page_png, approved_size=A4, revised_size=A4)
    assert result.status == "failed" and result.message


def test_different_page_shapes_are_not_compared(page_png):
    import cv2
    import numpy as np

    wide = cv2.imencode(".png", np.full((400, 900, 3), 255, np.uint8))[1].tobytes()
    result = compare_page_images(page_png, wide, approved_size=A4, revised_size=(842.0, 595.0))
    assert result.status == "not_comparable"
    assert "proportions" in result.message and not result.regions


def test_scans_are_never_pixel_compared(page_png):
    result = compare_page_images(page_png, page_png, approved_size=A4, revised_size=A4, scan_involved=True)
    assert result.status == "not_comparable" and not result.regions


def test_colour_change_is_found_and_highlighted(tmp_path, page_png):
    from dataclasses import replace

    other = render_page_png(render_label_pdf(replace(APPROVED, band_color="#8A2B2B"), tmp_path / "x.pdf").read_bytes(), 1, zoom=1.5)
    result = compare_page_images(page_png, other, approved_size=A4, revised_size=A4)
    assert result.status == "differences" and result.unexplained and result.overlay_png


def test_align_pages_pairs_by_content_not_position():
    old = ["alpha beta gamma delta", "epsilon zeta eta theta"]
    new = ["cover page unrelated words", "alpha beta gamma delta", "epsilon zeta eta theta"]
    pairs, only_old, only_new = align_pages(old, new)
    assert pairs == [(1, 2), (2, 3)] and only_old == [] and only_new == [1]


def test_align_pages_same_count_pairs_by_position_when_rewritten():
    pairs, only_old, only_new = align_pages(["aaa bbb ccc"], ["xxx yyy zzz"])
    assert pairs == [(1, 1)] and not only_old and not only_new


def test_barcode_decoded_only_when_a_value_is_returned(approved_pdf):
    scan = barcode.scan_pdf_barcodes(approved_pdf.read_bytes())
    assert scan.decoded and scan.items[0].digits.startswith("01")


def test_barcode_tool_unavailable_is_not_reported_as_decoded(monkeypatch, approved_pdf):
    monkeypatch.setattr(barcode, "_load_decoder", lambda: None)
    scan = barcode.scan_pdf_barcodes(approved_pdf.read_bytes())
    assert scan.status == "unavailable" and not scan.decoded and not scan.items
    assert barcode.barcode_decoder_status()[0] is False


def test_unreadable_pdf_gives_a_failed_scan_not_an_exception():
    scan = barcode.scan_pdf_barcodes(b"%PDF-broken")
    assert not scan.decoded and scan.status in ("failed", "none_found")

"""Visual (artwork) comparison of two PDFs, page by page, using OpenCV.

What this does: pairs up corresponding pages, brings both to the same size, corrects a small
misalignment, finds the areas where the pixels differ, and separates areas that are explained
by text edits found elsewhere (reported as text findings) from areas that are not.

What it does not do: decide that a visual change is wrong. A moved logo or a new colour may be
intended. It also cannot compare reliably when the pages have different proportions, when a
page cannot be rendered, or when one document is a scan of the other; in those cases it says so
instead of producing a confident-looking answer.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Sequence

from labelguard.comparison.models import BBox, TextDiff
from labelguard.extraction.pdf_render import render_page_png
from labelguard.extraction.pdf_text import ExtractedDocument
from labelguard.vision.images import decode_png, encode_png, vision_status

NORMALIZED_WIDTH = 1000  # both pages are scaled to this width before comparing
DIFF_THRESHOLD = 12  # difference (0-255, after blurring) in any colour channel that counts as "different"
MIN_REGION_PIXELS = 60  # smaller changed areas are treated as noise
ASPECT_TOLERANCE = 0.03  # pages whose width/height ratios differ more than this cannot be compared
MAX_ALIGN_SHIFT = 0.03  # largest misalignment (fraction of page width) that is corrected automatically
LARGE_DIFFERENCE_RATIO = 0.30  # if this much of the page differs, the comparison is flagged unreliable
EXPLAIN_PADDING_PX = 8
PAGE_MATCH_THRESHOLD = 0.5  # minimum text similarity for two pages to be paired when page counts differ

STATUS_IDENTICAL = "identical"
STATUS_TEXT_ONLY = "text_only"
STATUS_DIFFERENCES = "differences"
STATUS_NOT_COMPARABLE = "not_comparable"
STATUS_FAILED = "failed"


@dataclass(frozen=True)
class Region:
    """A changed area, in pixels of the normalized page image."""

    x: int
    y: int
    w: int
    h: int
    explained: bool  # True if it lies where text changes were already reported

    def as_percent(self, width: int, height: int) -> tuple[float, float, float, float]:
        return (round(100 * self.x / width, 1), round(100 * self.y / height, 1),
                round(100 * self.w / width, 1), round(100 * self.h / height, 1))


@dataclass(frozen=True)
class PageVisual:
    approved_page: int
    revised_page: int
    status: str
    message: str
    difference_ratio: float = 0.0  # share of the page's pixels that differ at all
    regions: tuple[Region, ...] = ()
    width: int = 0
    height: int = 0
    alignment_note: str = ""
    reliability_note: str | None = None
    approved_png: bytes | None = None
    revised_png: bytes | None = None
    overlay_png: bytes | None = None

    @property
    def unexplained(self) -> tuple[Region, ...]:
        return tuple(region for region in self.regions if not region.explained)

    @property
    def comparable(self) -> bool:
        return self.status in (STATUS_IDENTICAL, STATUS_TEXT_ONLY, STATUS_DIFFERENCES)


@dataclass(frozen=True)
class VisualComparison:
    status: str  # "ok", "unavailable" or "failed"
    message: str
    pages: tuple[PageVisual, ...] = ()
    unpaired_approved: tuple[int, ...] = ()
    unpaired_revised: tuple[int, ...] = ()


# --- Page pairing ---------------------------------------------------------------------
def _text_similarity(first: str, second: str) -> float:
    """Word-overlap similarity (0-1). Two empty pages are alike; an empty and a non-empty page are not."""
    a, b = Counter(first.lower().split()), Counter(second.lower().split())
    if not a and not b:
        return 1.0
    union = sum((a | b).values())
    return sum((a & b).values()) / union if union else 0.0


def align_pages(
    approved_texts: Sequence[str], revised_texts: Sequence[str], threshold: float = PAGE_MATCH_THRESHOLD
) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Pair corresponding pages by text similarity, keeping page order. Page numbers are 1-based.

    Returns (pairs, unpaired approved pages, unpaired revised pages). With equal page counts and
    no good text match, pages are paired by position.
    """
    n, m = len(approved_texts), len(revised_texts)
    score = [[_text_similarity(approved_texts[i], revised_texts[j]) + (0.01 if i == j else 0.0)
              for j in range(m)] for i in range(n)]
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            options = [best[i - 1][j], best[i][j - 1]]
            if score[i - 1][j - 1] >= threshold:
                options.append(best[i - 1][j - 1] + score[i - 1][j - 1])
            best[i][j] = max(options)

    pairs: list[tuple[int, int]] = []
    i, j = n, m
    while i > 0 and j > 0:
        if score[i - 1][j - 1] >= threshold and abs(best[i][j] - (best[i - 1][j - 1] + score[i - 1][j - 1])) < 1e-9:
            pairs.append((i, j))
            i, j = i - 1, j - 1
        elif best[i - 1][j] >= best[i][j - 1]:
            i -= 1
        else:
            j -= 1
    pairs.reverse()

    if n == m and len(pairs) < n:  # pages rewritten beyond recognition: assume the same order
        pairs = [(k, k) for k in range(1, n + 1)]
    paired_a, paired_b = {a for a, _ in pairs}, {b for _, b in pairs}
    return pairs, [k for k in range(1, n + 1) if k not in paired_a], [k for k in range(1, m + 1) if k not in paired_b]


# --- One page pair ------------------------------------------------------------------------
def compare_page_images(
    approved_png: bytes,
    revised_png: bytes,
    *,
    approved_size: tuple[float, float],
    revised_size: tuple[float, float],
    approved_explained: Sequence[BBox] = (),
    revised_explained: Sequence[BBox] = (),
    approved_page: int = 1,
    revised_page: int = 1,
    scan_involved: bool = False,
) -> PageVisual:
    """Compare two rendered pages. `*_explained` are page areas (PDF points) already covered by text findings.

    If `scan_involved` is true (either page is a scan), no pixel comparison is attempted: scanning changes
    resolution, edges and noise everywhere, so differences would not mean anything. The pages are still
    returned so they can be viewed side by side.

    Never raises: problems are returned as status "failed" or "not_comparable" with a reason.
    """

    def result(status: str, message: str, **extra) -> PageVisual:
        return PageVisual(approved_page, revised_page, status, message, **extra)

    try:
        import cv2
        import numpy as np

        old, new = decode_png(approved_png), decode_png(revised_png)
        old_h, old_w = old.shape[:2]
        new_h, new_w = new.shape[:2]
        if min(old_h, old_w, new_h, new_w) < 50:
            return result(STATUS_NOT_COMPARABLE, "A page image is too small to compare reliably.")
        if abs(old_w / old_h - new_w / new_h) / (old_w / old_h) > ASPECT_TOLERANCE:
            return result(
                STATUS_NOT_COMPARABLE,
                f"The pages have different proportions ({approved_size[0]:.0f} x {approved_size[1]:.0f} pt and "
                f"{revised_size[0]:.0f} x {revised_size[1]:.0f} pt), so they cannot be overlaid reliably.",
            )

        width = NORMALIZED_WIDTH
        height = round(width * old_h / old_w)
        old_n = cv2.resize(old, (width, height), interpolation=cv2.INTER_AREA)
        new_n = cv2.resize(new, (width, height), interpolation=cv2.INTER_AREA)

        if scan_involved:
            return result(
                STATUS_NOT_COMPARABLE,
                "At least one of these pages is a scan (an image without selectable text). A pixel comparison of a scan "
                "is not reliable, because scanning changes resolution, edges and noise everywhere. "
                "Compare the two pages side by side instead.",
                width=width, height=height, approved_png=encode_png(old_n), revised_png=encode_png(new_n),
            )

        new_n, alignment_note, alignment_warning = _align(cv2, np, old_n, new_n)

        diff = cv2.absdiff(cv2.GaussianBlur(old_n, (5, 5), 0), cv2.GaussianBlur(new_n, (5, 5), 0)).max(axis=2)
        changed = (diff >= DIFF_THRESHOLD).astype(np.uint8) * 255
        difference_ratio = float(np.count_nonzero(changed)) / changed.size

        explained_mask = np.zeros_like(changed)
        for boxes, size in ((approved_explained, approved_size), (revised_explained, revised_size)):
            for x0, y0, x1, y1 in boxes:
                sx, sy = width / size[0], height / size[1]
                cv2.rectangle(
                    explained_mask,
                    (max(0, int(x0 * sx) - EXPLAIN_PADDING_PX), max(0, int(y0 * sy) - EXPLAIN_PADDING_PX)),
                    (min(width - 1, int(x1 * sx) + EXPLAIN_PADDING_PX), min(height - 1, int(y1 * sy) + EXPLAIN_PADDING_PX)),
                    255, -1,
                )
        regions = _regions(cv2, np, cv2.bitwise_and(changed, cv2.bitwise_not(explained_mask)), explained=False)
        regions += _regions(cv2, np, cv2.bitwise_and(changed, explained_mask), explained=True)

        notes = [alignment_warning] if alignment_warning else []
        if difference_ratio > LARGE_DIFFERENCE_RATIO:
            notes.append(
                f"{difference_ratio:.0%} of the page differs. This usually means different scale or resolution, a scan "
                "compared with a digital file, or a heavily redesigned page, so the highlighted regions are unreliable."
            )

        unexplained = [region for region in regions if not region.explained]
        if not regions:
            status, message = STATUS_IDENTICAL, "No visual differences were detected."
        elif not unexplained:
            status = STATUS_TEXT_ONLY
            message = f"Visual differences are limited to areas where text changed ({len(regions)} region(s))."
        else:
            status = STATUS_DIFFERENCES
            message = f"{len(unexplained)} region(s) differ outside areas of changed text."
        return result(
            status, message,
            difference_ratio=difference_ratio,
            regions=tuple(regions),
            width=width,
            height=height,
            alignment_note=alignment_note,
            reliability_note=" ".join(notes) or None,
            approved_png=encode_png(old_n),
            revised_png=encode_png(new_n),
            overlay_png=encode_png(_overlay(cv2, np, new_n, regions)),
        )
    except Exception as exc:  # image problems must never stop an analysis
        return result(STATUS_FAILED, f"The visual comparison failed ({exc.__class__.__name__}: {exc}).")


def _align(cv2, np, old, new):
    """Correct a small translation between the pages. Returns (new image, note, warning)."""
    window = cv2.createHanningWindow((old.shape[1], old.shape[0]), cv2.CV_32F)
    gray_old = cv2.cvtColor(old, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gray_new = cv2.cvtColor(new, cv2.COLOR_BGR2GRAY).astype(np.float32)
    (dx, dy), response = cv2.phaseCorrelate(gray_old, gray_new, window)
    magnitude = max(abs(dx), abs(dy))
    if response < 0.2 or magnitude < 0.5:
        return new, "No alignment correction was needed.", None
    if magnitude > MAX_ALIGN_SHIFT * old.shape[1]:
        return new, "", (
            f"The pages may be offset by about {dx:.0f} px horizontally and {dy:.0f} px vertically, which is too "
            "much to correct automatically; differences near the edges may be caused by the offset."
        )
    matrix = np.float32([[1, 0, -dx], [0, 1, -dy]])
    shifted = cv2.warpAffine(new, matrix, (old.shape[1], old.shape[0]), borderValue=(255, 255, 255))
    return shifted, f"The revised page was shifted by ({-dx:.1f}, {-dy:.1f}) px to line it up.", None


def _regions(cv2, np, mask, explained: bool) -> tuple[Region, ...]:
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    found = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if cv2.countNonZero(mask[y:y + h, x:x + w]) >= MIN_REGION_PIXELS:
            found.append(Region(int(x), int(y), int(w), int(h), explained))
    return tuple(sorted(found, key=lambda r: (r.y, r.x)))


def _overlay(cv2, np, image, regions: Sequence[Region]):
    """The revised page with unexplained regions in red and text-explained regions in amber."""
    base = image.copy()
    shade = image.copy()
    for region in regions:
        if not region.explained:
            cv2.rectangle(shade, (region.x, region.y), (region.x + region.w, region.y + region.h), (0, 0, 230), -1)
    base = cv2.addWeighted(shade, 0.18, base, 0.82, 0)
    for region in regions:
        colour, thickness = ((0, 170, 255), 2) if region.explained else ((0, 0, 230), 3)
        cv2.rectangle(base, (region.x, region.y), (region.x + region.w, region.y + region.h), colour, thickness)
    return base


# --- Whole documents --------------------------------------------------------------------------
def compare_documents(
    approved_pdf: bytes,
    revised_pdf: bytes,
    approved_doc: ExtractedDocument,
    revised_doc: ExtractedDocument,
    text_diff: TextDiff | None = None,
    extra_explained: dict[tuple[str, int], list[BBox]] | None = None,
    zoom: float = 2.0,
) -> VisualComparison:
    """Compare two PDFs visually. Never raises; returns status "unavailable" or "failed" with a reason."""
    available, message = vision_status()
    if not available:
        return VisualComparison("unavailable", message)
    try:
        pairs, unpaired_a, unpaired_b = align_pages(
            [page.text for page in approved_doc.pages], [page.text for page in revised_doc.pages]
        )
        boxes = dict(text_diff.affected_boxes()) if text_diff else {}
        for key, extra in (extra_explained or {}).items():
            boxes[key] = boxes.get(key, []) + list(extra)

        pages: list[PageVisual] = []
        for a, b in pairs:
            try:
                old_png = render_page_png(approved_pdf, a, zoom)
                new_png = render_page_png(revised_pdf, b, zoom)
            except Exception as exc:
                pages.append(PageVisual(a, b, STATUS_FAILED, f"Page could not be rendered ({exc.__class__.__name__}: {exc})."))
                continue
            old_page, new_page = approved_doc.pages[a - 1], revised_doc.pages[b - 1]
            pages.append(compare_page_images(
                old_png, new_png,
                approved_size=(old_page.width, old_page.height),
                revised_size=(new_page.width, new_page.height),
                approved_explained=boxes.get(("approved", a), []),
                revised_explained=boxes.get(("revised", b), []),
                approved_page=a,
                revised_page=b,
                scan_involved=any(p.source == "ocr" or p.needs_ocr for p in (old_page, new_page)),
            ))
        return VisualComparison("ok", f"{len(pages)} page pair(s) compared.", tuple(pages), tuple(unpaired_a), tuple(unpaired_b))
    except Exception as exc:
        return VisualComparison("failed", f"The visual comparison could not be completed ({exc.__class__.__name__}: {exc}).")

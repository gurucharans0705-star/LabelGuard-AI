"""Result types for label comparison.

These describe *differences*, not violations. A difference may be an intended,
approved change; deciding that is the reviewer's job.
"""
from __future__ import annotations

from dataclasses import dataclass

from labelguard.fields.models import FIELD_LABELS, FieldHit

KIND_LABELS = {
    "unchanged": "Exact match",
    "modified": "Modified",
    "added": "Added in revised label",
    "removed": "Missing from revised label",
}


@dataclass(frozen=True)
class ValueChange:
    """One value of a field, compared between the two labels."""

    kind: str  # "unchanged" | "modified" | "added" | "removed"
    approved: FieldHit | None
    revised: FieldHit | None
    similarity: float | None = None


@dataclass(frozen=True)
class FieldComparison:
    key: str
    changes: tuple[ValueChange, ...] = ()

    @property
    def label(self) -> str:
        return FIELD_LABELS[self.key]

    @property
    def status(self) -> str:
        """"not_detected" (in neither label), "match" (all values identical) or "changed"."""
        if not self.changes:
            return "not_detected"
        if all(change.kind == "unchanged" for change in self.changes):
            return "match"
        return "changed"


BBox = tuple[float, float, float, float]  # x0, y0, x1, y1 in PDF points


@dataclass(frozen=True)
class TextChange:
    kind: str  # "added" | "removed" | "modified"
    approved_text: str | None
    revised_text: str | None
    approved_page: int | None = None
    revised_page: int | None = None
    similarity: float | None = None
    approved_bbox: BBox | None = None
    revised_bbox: BBox | None = None


@dataclass(frozen=True)
class MovedLine:
    """A line with identical wording that is in a different place (moved, reordered or shifted)."""

    text: str
    approved_page: int
    revised_page: int
    approved_bbox: BBox
    revised_bbox: BBox


@dataclass(frozen=True)
class TextDiff:
    """Detailed line comparison, including positions, so other checks can explain what moved."""

    changes: tuple[TextChange, ...]
    unchanged: int  # lines equal in wording and order
    moved: tuple[MovedLine, ...] = ()  # same wording, different order: not a content change
    shifted: tuple[MovedLine, ...] = ()  # same wording and order, printed at a different position
    # True when words were compared instead of lines (used when a label was read by OCR, because
    # OCR splits and joins lines differently from the original PDF). Moves are not detected then.
    word_level: bool = False

    def affected_boxes(self) -> dict[tuple[str, int], list[BBox]]:
        """Areas of the pages touched by text edits, keyed by ("approved" | "revised", page).

        Shifted lines are only counted when other text changed too: then the shift is most
        likely re-flow caused by the edit. On its own, a shift is a layout change and stays visible.
        """
        boxes: dict[tuple[str, int], list[BBox]] = {}

        def add(side: str, page: int | None, box: BBox | None) -> None:
            if page is not None and box is not None:
                boxes.setdefault((side, page), []).append(box)

        for change in self.changes:
            add("approved", change.approved_page, change.approved_bbox)
            add("revised", change.revised_page, change.revised_bbox)
        for line in self.moved:
            add("approved", line.approved_page, line.approved_bbox)
            add("revised", line.revised_page, line.revised_bbox)
        if self.changes or self.moved:
            for line in self.shifted:
                add("approved", line.approved_page, line.approved_bbox)
                add("revised", line.revised_page, line.revised_bbox)
        return boxes


@dataclass(frozen=True)
class ComparisonResult:
    fields: tuple[FieldComparison, ...]
    text_changes: tuple[TextChange, ...]
    unchanged_lines: int
    text_diff: TextDiff | None = None

    @property
    def moved_lines(self) -> int:
        """Number of lines whose wording is unchanged but whose order or place differs."""
        return len(self.text_diff.moved) if self.text_diff else 0

    @property
    def matching_fields(self) -> int:
        return sum(1 for item in self.fields if item.status == "match")

    @property
    def changed_fields(self) -> int:
        return sum(1 for item in self.fields if item.status == "changed")

    @property
    def undetected_fields(self) -> int:
        return sum(1 for item in self.fields if item.status == "not_detected")

    def count_text(self, kind: str) -> int:
        return sum(1 for change in self.text_changes if change.kind == kind)

    @property
    def text_identical(self) -> bool:
        return not self.text_changes

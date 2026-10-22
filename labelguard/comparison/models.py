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


@dataclass(frozen=True)
class TextChange:
    kind: str  # "added" | "removed" | "modified"
    approved_text: str | None
    revised_text: str | None
    approved_page: int | None = None
    revised_page: int | None = None
    similarity: float | None = None


@dataclass(frozen=True)
class ComparisonResult:
    fields: tuple[FieldComparison, ...]
    text_changes: tuple[TextChange, ...]
    unchanged_lines: int

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

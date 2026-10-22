"""Compare two labels: field by field, and line by line.

Matching ignores case, repeated spaces and dash style, so formatting-only
differences are not reported. Everything else is reported neutrally as added,
removed or modified text; nothing here decides whether a change is acceptable.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from labelguard.comparison.models import ComparisonResult, FieldComparison, TextChange, ValueChange
from labelguard.extraction.pdf_text import TextLine
from labelguard.fields.models import FIELD_LABELS, MULTI_VALUED, FieldHit, LabelFields

FIELD_PAIR_THRESHOLD = 0.6  # how alike two statements must be to count as "modified" rather than removed + added
LINE_PAIR_THRESHOLD = 0.5

_DASHES = str.maketrans({"–": "-", "—": "-", "−": "-"})


def comparison_key(text: str) -> str:
    """Form of `text` used to decide whether two values are the same."""
    cleaned = re.sub(r"\s+", " ", text.translate(_DASHES).casefold())
    return cleaned.strip(" .,;:")


def similarity(first: str, second: str) -> float:
    """0-1 similarity of two texts (1 means identical after normalisation)."""
    return SequenceMatcher(None, comparison_key(first), comparison_key(second), autojunk=False).ratio()


def _pair_by_similarity(first: list[str], second: list[str], threshold: float) -> list[tuple[int, int, float]]:
    """Greedily pair the most similar items (each used once) with similarity >= threshold."""
    scored = sorted(
        ((similarity(a, b), i, j) for i, a in enumerate(first) for j, b in enumerate(second)),
        reverse=True,
    )
    used_first: set[int] = set()
    used_second: set[int] = set()
    pairs: list[tuple[int, int, float]] = []
    for score, i, j in scored:
        if score < threshold:
            break
        if i in used_first or j in used_second:
            continue
        used_first.add(i)
        used_second.add(j)
        pairs.append((i, j, score))
    return sorted(pairs)


def _unique_hits(hits: tuple[FieldHit, ...]) -> list[FieldHit]:
    seen: set[str] = set()
    unique: list[FieldHit] = []
    for hit in hits:
        key = comparison_key(hit.value)
        if key not in seen:
            seen.add(key)
            unique.append(hit)
    return unique


def compare_field(key: str, approved: tuple[FieldHit, ...], revised: tuple[FieldHit, ...]) -> FieldComparison:
    """Compare the values detected for one field in the two labels."""
    approved_hits = _unique_hits(approved)
    revised_hits = _unique_hits(revised)
    revised_by_key = {comparison_key(hit.value): hit for hit in revised_hits}

    changes: list[ValueChange] = []
    approved_rest: list[FieldHit] = []
    for hit in approved_hits:
        match = revised_by_key.get(comparison_key(hit.value))
        if match is not None:
            changes.append(ValueChange("unchanged", hit, match, 1.0))
        else:
            approved_rest.append(hit)
    matched_keys = {comparison_key(hit.value) for hit in approved_hits}
    revised_rest = [hit for hit in revised_hits if comparison_key(hit.value) not in matched_keys]

    if key not in MULTI_VALUED and len(approved_rest) == 1 and len(revised_rest) == 1:
        # A single-valued field with one value on each side: the value was changed.
        pairs = [(0, 0, similarity(approved_rest[0].value, revised_rest[0].value))]
    else:
        pairs = _pair_by_similarity(
            [hit.value for hit in approved_rest], [hit.value for hit in revised_rest], FIELD_PAIR_THRESHOLD
        )

    paired_a = {i for i, _, _ in pairs}
    paired_b = {j for _, j, _ in pairs}
    changes.extend(ValueChange("modified", approved_rest[i], revised_rest[j], score) for i, j, score in pairs)
    changes.extend(ValueChange("removed", hit, None) for i, hit in enumerate(approved_rest) if i not in paired_a)
    changes.extend(ValueChange("added", None, hit) for j, hit in enumerate(revised_rest) if j not in paired_b)
    return FieldComparison(key, tuple(changes))


def compare_fields(approved: LabelFields, revised: LabelFields) -> tuple[FieldComparison, ...]:
    return tuple(compare_field(key, approved.get(key).hits, revised.get(key).hits) for key in FIELD_LABELS)


def compare_text(approved_lines: list[TextLine], revised_lines: list[TextLine]) -> tuple[list[TextChange], int]:
    """Line-by-line comparison. Returns (changes, number of unchanged lines)."""
    approved_keys = [comparison_key(line.text) for line in approved_lines]
    revised_keys = [comparison_key(line.text) for line in revised_lines]
    matcher = SequenceMatcher(None, approved_keys, revised_keys, autojunk=False)

    changes: list[TextChange] = []
    unchanged = 0
    for tag, a1, a2, b1, b2 in matcher.get_opcodes():
        if tag == "equal":
            unchanged += a2 - a1
            continue
        old = approved_lines[a1:a2]
        new = revised_lines[b1:b2]
        pairs = _pair_by_similarity([l.text for l in old], [l.text for l in new], LINE_PAIR_THRESHOLD)
        paired_old = {i for i, _, _ in pairs}
        paired_new = {j for _, j, _ in pairs}
        for i, j, score in pairs:
            changes.append(TextChange("modified", old[i].text, new[j].text, old[i].page, new[j].page, score))
        for i, line in enumerate(old):
            if i not in paired_old:
                changes.append(TextChange("removed", line.text, None, line.page, None))
        for j, line in enumerate(new):
            if j not in paired_new:
                changes.append(TextChange("added", None, line.text, None, line.page))
    return changes, unchanged


def inline_diff(approved_text: str, revised_text: str) -> list[tuple[str, str]]:
    """Word-level diff as (kind, text) segments; kind is "equal", "removed" or "added"."""
    old, new = approved_text.split(), revised_text.split()
    matcher = SequenceMatcher(None, [w.casefold() for w in old], [w.casefold() for w in new], autojunk=False)
    segments: list[tuple[str, str]] = []
    for tag, a1, a2, b1, b2 in matcher.get_opcodes():
        if tag == "equal":
            segments.append(("equal", " ".join(old[a1:a2])))
            continue
        if a2 > a1:
            segments.append(("removed", " ".join(old[a1:a2])))
        if b2 > b1:
            segments.append(("added", " ".join(new[b1:b2])))
    return segments


def compare_labels(
    approved_lines: list[TextLine],
    approved_fields: LabelFields,
    revised_lines: list[TextLine],
    revised_fields: LabelFields,
) -> ComparisonResult:
    """Full comparison of an approved and a revised label."""
    text_changes, unchanged = compare_text(approved_lines, revised_lines)
    return ComparisonResult(
        fields=compare_fields(approved_fields, revised_fields),
        text_changes=tuple(text_changes),
        unchanged_lines=unchanged,
    )

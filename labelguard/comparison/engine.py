"""Compare two labels: field by field, and line by line.

Matching ignores case, repeated spaces and dash style, so formatting-only
differences are not reported. Everything else is reported neutrally as added,
removed or modified text; nothing here decides whether a change is acceptable.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from labelguard.comparison.models import (
    ComparisonResult,
    FieldComparison,
    MovedLine,
    TextChange,
    TextDiff,
    ValueChange,
)
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


POSITION_TOLERANCE = 2.0  # points; smaller position differences are rendering noise


def compare_text_detailed(approved_lines: list[TextLine], revised_lines: list[TextLine]) -> TextDiff:
    """Line-by-line comparison that also recognises reordered lines and keeps positions.

    A line that disappears in one place and reappears with identical wording elsewhere is
    reported as *moved*, not as removed plus added: reordering text is not a content change.
    """
    approved_keys = [comparison_key(line.text) for line in approved_lines]
    revised_keys = [comparison_key(line.text) for line in revised_lines]
    matcher = SequenceMatcher(None, approved_keys, revised_keys, autojunk=False)

    changes: list[TextChange] = []
    shifted: list[MovedLine] = []
    unchanged = 0
    for tag, a1, a2, b1, b2 in matcher.get_opcodes():
        if tag == "equal":
            unchanged += a2 - a1
            for offset in range(a2 - a1):
                old, new = approved_lines[a1 + offset], revised_lines[b1 + offset]
                if old.page != new.page or max(abs(o - n) for o, n in zip(old.bbox, new.bbox)) > POSITION_TOLERANCE:
                    shifted.append(MovedLine(old.text, old.page, new.page, old.bbox, new.bbox))
            continue
        old_block = approved_lines[a1:a2]
        new_block = revised_lines[b1:b2]
        pairs = _pair_by_similarity([l.text for l in old_block], [l.text for l in new_block], LINE_PAIR_THRESHOLD)
        paired_old = {i for i, _, _ in pairs}
        paired_new = {j for _, j, _ in pairs}
        for i, j, score in pairs:
            old, new = old_block[i], new_block[j]
            changes.append(TextChange("modified", old.text, new.text, old.page, new.page, score, old.bbox, new.bbox))
        for i, line in enumerate(old_block):
            if i not in paired_old:
                changes.append(TextChange("removed", line.text, None, line.page, None, None, line.bbox, None))
        for j, line in enumerate(new_block):
            if j not in paired_new:
                changes.append(TextChange("added", None, line.text, None, line.page, None, None, line.bbox))

    changes, moved = _extract_moved_lines(changes, approved_lines, revised_lines)
    return TextDiff(tuple(changes), unchanged, tuple(moved), tuple(shifted))


def _extract_moved_lines(
    changes: list[TextChange], approved_lines: list[TextLine], revised_lines: list[TextLine]
) -> tuple[list[TextChange], list[MovedLine]]:
    """Pair removed and added lines that have identical wording; they were moved, not edited."""
    removed_by_key: dict[str, list[TextChange]] = {}
    for change in changes:
        if change.kind == "removed":
            removed_by_key.setdefault(comparison_key(change.approved_text or ""), []).append(change)

    moved: list[MovedLine] = []
    dropped: set[int] = set()
    for change in changes:
        if change.kind != "added":
            continue
        candidates = removed_by_key.get(comparison_key(change.revised_text or ""))
        if candidates and comparison_key(change.revised_text or ""):
            partner = candidates.pop(0)
            dropped.update((id(change), id(partner)))
            moved.append(MovedLine(
                change.revised_text or "", partner.approved_page or 0, change.revised_page or 0,
                partner.approved_bbox or (0.0, 0.0, 0.0, 0.0), change.revised_bbox or (0.0, 0.0, 0.0, 0.0),
            ))
    return [change for change in changes if id(change) not in dropped], moved


_BULLETS = frozenset({"-", "•", "–", "—", "*", "▪", "●"})


def _word_tokens(lines: list[TextLine]) -> list[tuple[str, int]]:
    """(comparison key, line index) for every word; bullets and empty words are ignored."""
    tokens: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        for word in line.text.split():
            key = comparison_key(word)
            if key and key not in _BULLETS:
                tokens.append((key, index))
    return tokens


def _line_indexes(lines: list[TextLine], tokens: list[tuple[str, int]], start: int, stop: int) -> list[int]:
    """Indexes of the distinct lines that hold tokens[start:stop], in order."""
    seen: list[int] = []
    for _, line_index in tokens[start:stop]:
        if line_index not in seen:
            seen.append(line_index)
    return seen


def compare_text_words(approved_lines: list[TextLine], revised_lines: list[TextLine]) -> TextDiff:
    """Word-by-word comparison, for labels read by OCR.

    OCR often splits or joins lines differently from the original PDF ("REF ... STERILE EO" on one
    line instead of two). Comparing lines would report that as many changes. Here the words of each
    label are lined up instead, so only real differences in wording remain. Each change is reported
    with the whole line it sits in, so the reviewer sees it in context. Reordered lines are not
    recognised in this mode.
    """
    old_tokens, new_tokens = _word_tokens(approved_lines), _word_tokens(revised_lines)
    matcher = SequenceMatcher(None, [t[0] for t in old_tokens], [t[0] for t in new_tokens], autojunk=False)

    changes: list[TextChange] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    touched_old: set[int] = set()

    def add(kind: str, old_idx: list[int], new_idx: list[int], score: float | None) -> None:
        old = [approved_lines[i] for i in old_idx]
        new = [revised_lines[i] for i in new_idx]
        old_text = " ".join(line.text for line in old) or None
        new_text = " ".join(line.text for line in new) or None
        if (kind, old_text, new_text) in seen:
            return
        seen.add((kind, old_text, new_text))
        changes.append(TextChange(
            kind, old_text, new_text,
            old[0].page if old else None, new[0].page if new else None, score,
            old[0].bbox if old else None, new[0].bbox if new else None,
        ))

    for tag, a1, a2, b1, b2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        old_idx = _line_indexes(approved_lines, old_tokens, a1, a2)
        new_idx = _line_indexes(revised_lines, new_tokens, b1, b2)
        touched_old.update(old_idx)
        if tag == "replace":
            add("modified", old_idx, new_idx, similarity(
                " ".join(approved_lines[i].text for i in old_idx), " ".join(revised_lines[i].text for i in new_idx)))
            continue
        # A pure deletion or insertion: if the other label has a similar line right where the words
        # were, a word was dropped from / added to a line (modified); otherwise a whole line is added or removed.
        if tag == "delete":
            own, other_lines, other_tokens, near = old_idx, revised_lines, new_tokens, (b1 - 1, b1)
        else:
            own, other_lines, other_tokens, near = new_idx, approved_lines, old_tokens, (a1 - 1, a1)
        own_lines = approved_lines if tag == "delete" else revised_lines
        own_text = " ".join(own_lines[i].text for i in own)
        best_index, best_score = None, 0.0
        for position in near:
            if 0 <= position < len(other_tokens):
                line_index = other_tokens[position][1]
                score = similarity(own_text, other_lines[line_index].text)
                if score > best_score:
                    best_index, best_score = line_index, score
        if best_index is not None and best_score >= LINE_PAIR_THRESHOLD:
            if tag == "delete":
                add("modified", own, [best_index], best_score)
            else:
                add("modified", [best_index], own, best_score)
        elif tag == "delete":
            add("removed", own, [], None)
        else:
            add("added", [], own, None)

    unchanged = sum(1 for index in range(len(approved_lines)) if index not in touched_old)
    return TextDiff(tuple(changes), unchanged, (), (), word_level=True)


def compare_text(approved_lines: list[TextLine], revised_lines: list[TextLine]) -> tuple[list[TextChange], int]:
    """Line-by-line comparison. Returns (changes, number of lines with unchanged wording)."""
    diff = compare_text_detailed(approved_lines, revised_lines)
    return list(diff.changes), diff.unchanged + len(diff.moved)


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
    *,
    word_level: bool = False,
) -> ComparisonResult:
    """Full comparison of an approved and a revised label.

    Set `word_level` when either label was read by OCR (see compare_text_words).
    """
    compare = compare_text_words if word_level else compare_text_detailed
    diff = compare(approved_lines, revised_lines)
    return ComparisonResult(
        fields=compare_fields(approved_fields, revised_fields),
        text_changes=diff.changes,
        unchanged_lines=diff.unchanged + len(diff.moved),
        text_diff=diff,
    )

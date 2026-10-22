"""Turn a comparison into tables and a Markdown report.

Day 1 scope: a plain-text report a reviewer can read, save or paste into a
ticket. A formatted PDF report with page images is planned for Day 4. The report
describes differences neutrally and never states that a label is compliant.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from labelguard.comparison.models import KIND_LABELS, ComparisonResult
from labelguard.fields.models import FIELD_LABELS, NOT_DETECTED

if TYPE_CHECKING:
    from labelguard.pipeline import AnalysisResult, LabelAnalysis

NOT_DETECTED_BOTH = "Not detected in either label"

DISCLAIMER = (
    "This report was produced by LabelGuard AI, a decision-support tool. It lists differences and "
    "possible omissions found by text extraction and rules. It does not establish or certify "
    "regulatory compliance, and a qualified person must review every item against the source labels."
)


def field_rows(comparison: ComparisonResult) -> list[dict[str, str]]:
    """Flatten the field comparison into one table row per compared value."""
    rows: list[dict[str, str]] = []
    for item in comparison.fields:
        if not item.changes:
            rows.append({
                "Field": item.label,
                "Approved label": NOT_DETECTED,
                "Revised label": NOT_DETECTED,
                "Result": NOT_DETECTED_BOTH,
            })
            continue
        for change in item.changes:
            rows.append({
                "Field": item.label,
                "Approved label": change.approved.value if change.approved else "Not present",
                "Revised label": change.revised.value if change.revised else "Not present",
                "Result": KIND_LABELS[change.kind],
            })
    return rows


def _cell(text: str) -> str:
    """Make text safe inside a Markdown table cell."""
    return " ".join(str(text).split()).replace("|", "\\|")


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"]
    lines.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return lines


def _where(page: int | None) -> str:
    return f"page {page}" if page else "page not recorded"


def _evidence_lines(label: str, analysis: "LabelAnalysis", comparison: ComparisonResult) -> list[str]:
    """Quote the label text behind each changed field value, so the reviewer can verify it."""
    lines: list[str] = []
    for item in comparison.fields:
        for change in item.changes:
            if change.kind == "unchanged":
                continue
            hit = change.approved if label == "Approved label" else change.revised
            if hit is not None:
                lines.append(f"- **{item.label}**, {_where(hit.page)}: \"{_cell(hit.evidence)}\"")
    return lines


def comparison_to_markdown(result: "AnalysisResult", generated_at: datetime | None = None) -> str:
    """Build the Markdown comparison report for an analysis."""
    stamp = (generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    comparison = result.comparison
    labels = (("Approved label", result.approved), ("Revised label", result.revised))

    out: list[str] = ["# LabelGuard AI - label comparison report", "", f"Generated: {stamp}", "", f"> {DISCLAIMER}", ""]

    out += ["## Files compared", ""]
    out += _table(
        ["Label", "File", "Pages", "Pages read by OCR", "SHA-256"],
        [
            [title, a.document.filename, str(a.document.page_count),
             ", ".join(map(str, a.document.ocr_pages)) or "none", a.document.sha256]
            for title, a in labels
        ],
    )

    notes = [(title, note) for title, a in labels for note in a.ingestion.notes if note.level == "warning"]
    if notes:
        out += ["", "## Processing warnings", ""]
        out += [f"- **{title}:** {note.message}" for title, note in notes]

    out += [
        "", "## Summary", "",
        f"- Fields with an exact match: {comparison.matching_fields}",
        f"- Fields with differences: {comparison.changed_fields}",
        f"- Fields not detected in either label: {comparison.undetected_fields}",
        f"- Text lines identical: {comparison.unchanged_lines}",
        f"- Text lines modified / added / removed: {comparison.count_text('modified')} / "
        f"{comparison.count_text('added')} / {comparison.count_text('removed')}",
        "", "## Field comparison", "",
    ]
    out += _table(["Field", "Approved label", "Revised label", "Result"],
                  [list(row.values()) for row in field_rows(comparison)])

    out += ["", "## Text differences", ""]
    if comparison.text_identical:
        out.append("The text of the two labels is identical.")
    else:
        out += _table(
            ["Type", "Approved label", "Revised label", "Page"],
            [
                [KIND_LABELS[c.kind], c.approved_text or "-", c.revised_text or "-",
                 str(c.approved_page or c.revised_page or "")]
                for c in comparison.text_changes
            ],
        )

    out += ["", "## Evidence for changed fields", ""]
    for title, analysis in labels:
        quotes = _evidence_lines(title, analysis, comparison)
        out += [f"### {title}", ""] + (quotes or ["No changed field values were detected on this label."]) + [""]

    out += ["## Completeness observations", ""]
    for title, analysis in labels:
        out += [f"### {title}", ""]
        if analysis.observations:
            out += [f"- ({o.level}) {FIELD_LABELS[o.field]}: {o.message}" for o in analysis.observations]
        else:
            out.append("All expected elements were detected.")
        out.append("")

    out += ["---", "", "Differences are neutral observations. A difference is not automatically an error: "
            "many label changes are intended and approved. OCR text can contain misread characters."]
    return "\n".join(out) + "\n"

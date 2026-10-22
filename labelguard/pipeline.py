"""End-to-end analysis: ingest two labels, extract fields, compare and check them.

This is the single entry point the user interface calls, so all business logic
stays outside Streamlit and can be tested without it.
"""
from __future__ import annotations

from dataclasses import dataclass

from labelguard.comparison.engine import compare_labels
from labelguard.comparison.models import ComparisonResult
from labelguard.extraction.pdf_text import ExtractionError
from labelguard.fields.extractor import extract_fields
from labelguard.fields.models import LabelFields
from labelguard.ingestion import IngestionResult, ProgressCallback, ingest_pdf
from labelguard.validation.checks import Observation, check_label


@dataclass(frozen=True)
class LabelAnalysis:
    """Everything learned about one label."""

    ingestion: IngestionResult
    fields: LabelFields
    observations: tuple[Observation, ...]

    @property
    def document(self):
        return self.ingestion.document


@dataclass(frozen=True)
class AnalysisResult:
    approved: LabelAnalysis
    revised: LabelAnalysis
    comparison: ComparisonResult


def analyze_label(
    data: bytes, filename: str, *, use_ocr: bool = True, progress: ProgressCallback | None = None
) -> LabelAnalysis:
    """Ingest one label PDF and extract its fields. Raises ExtractionError for unusable files."""
    ingestion = ingest_pdf(data, filename, use_ocr=use_ocr, progress=progress)
    fields = extract_fields(ingestion.document)
    return LabelAnalysis(ingestion, fields, check_label(fields))


def analyze_pair(
    approved: tuple[bytes, str],
    revised: tuple[bytes, str],
    *,
    use_ocr: bool = True,
    progress: ProgressCallback | None = None,
) -> AnalysisResult:
    """Analyse an approved and a revised label (each given as (bytes, filename)) and compare them."""
    say = progress or (lambda message: None)
    say("Processing the approved label")
    try:
        approved_result = analyze_label(*approved, use_ocr=use_ocr, progress=progress)
    except ExtractionError as exc:
        raise ExtractionError(f"Approved label ({approved[1]}): {exc}") from exc
    say("Processing the revised label")
    try:
        revised_result = analyze_label(*revised, use_ocr=use_ocr, progress=progress)
    except ExtractionError as exc:
        raise ExtractionError(f"Revised label ({revised[1]}): {exc}") from exc
    say("Comparing the two labels")
    comparison = compare_labels(
        approved_result.document.all_lines,
        approved_result.fields,
        revised_result.document.all_lines,
        revised_result.fields,
    )
    return AnalysisResult(approved_result, revised_result, comparison)

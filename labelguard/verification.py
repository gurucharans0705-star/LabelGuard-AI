"""Verification: run the whole Day 2 engine on an approved and a revised label.

This is the single entry point the user interface calls for verification. It extends the Day 1
analysis (text, fields, comparison) with barcode decoding, visual comparison and the configured
rules, and returns findings with evidence. Everything here is deterministic and runs locally.

Findings are discrepancies or items that could not be verified, ranked by workflow priority.
They are not regulatory conclusions, and nothing in this module says a label is compliant.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from labelguard.ingestion import ProgressCallback
from labelguard.pipeline import AnalysisResult, analyze_pair
from labelguard.rules.engine import RuleContext, evaluate_rules
from labelguard.rules.loader import load_rules
from labelguard.rules.models import Rule, VerificationReport
from labelguard.vision.barcode import BarcodeScan, scan_pdf_barcodes
from labelguard.vision.compare import VisualComparison, compare_documents

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VerificationResult:
    """Everything produced by one verification run."""

    analysis: AnalysisResult
    report: VerificationReport
    visual: VisualComparison | None
    approved_barcodes: BarcodeScan | None
    revised_barcodes: BarcodeScan | None
    rules: tuple[Rule, ...]
    rules_source: str


def verify_pair(
    approved: tuple[bytes, str],
    revised: tuple[bytes, str],
    *,
    use_ocr: bool = True,
    rules: Sequence[Rule] | None = None,
    rules_path: Path | str | None = None,
    run_visual: bool = True,
    run_barcodes: bool = True,
    progress: ProgressCallback | None = None,
) -> VerificationResult:
    """Analyse, compare and verify two labels (each given as (pdf bytes, file name)).

    Raises ExtractionError for an unusable PDF and RuleConfigError for an unusable rules file.
    Visual comparison and barcode decoding never raise: if they cannot run, the result says why,
    and the rules report "not evaluated" instead of "passed".
    """
    say = progress or (lambda message: None)
    # Load the rules first: a broken rules file should fail fast, before minutes of OCR.
    if rules is None:
        rules = load_rules(rules_path)
        source = str(rules_path) if rules_path is not None else "config/rules.toml"
    else:
        source = "supplied rules"

    analysis = analyze_pair(approved, revised, use_ocr=use_ocr, progress=progress)

    approved_scan = revised_scan = None
    if run_barcodes:
        say("Looking for barcodes")
        approved_scan = scan_pdf_barcodes(approved[0])
        revised_scan = scan_pdf_barcodes(revised[0])

    visual = None
    if run_visual:
        say("Comparing artwork")
        visual = compare_documents(
            approved[0], revised[0], analysis.approved.document, analysis.revised.document,
            text_diff=analysis.comparison.text_diff,
        )

    say("Applying validation rules")
    context = RuleContext(
        approved_fields=analysis.approved.fields,
        revised_fields=analysis.revised.fields,
        approved_doc=analysis.approved.document,
        revised_doc=analysis.revised.document,
        comparison=analysis.comparison,
        visual=visual,
        approved_barcodes=approved_scan,
        revised_barcodes=revised_scan,
        read_warnings={
            "approved": tuple(n.message for n in analysis.approved.ingestion.notes if n.level == "warning"),
            "revised": tuple(n.message for n in analysis.revised.ingestion.notes if n.level == "warning"),
        },
    )
    report = evaluate_rules(rules, context)
    return VerificationResult(analysis, report, visual, approved_scan, revised_scan, tuple(rules), source)

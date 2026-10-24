"""Data types for validation rules, findings and their evidence.

Wording matters here. A *finding* is a discrepancy or an unverified item that a person
should look at. A *severity* is a workflow priority for that person; it is not proof of a
regulatory violation, and nothing in these types states that a label is or is not compliant.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

SEVERITIES: tuple[str, ...] = ("critical_review", "high", "medium", "low", "informational")
SEVERITY_LABELS: dict[str, str] = {
    "critical_review": "Critical review",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
    "informational": "Informational",
}
SEVERITY_NOTE = (
    "Severity is a workflow priority that helps a reviewer decide what to look at first. "
    "It is not a regulatory classification and not evidence of a violation."
)

CATEGORIES: dict[str, str] = {
    "added_content": "Added content",
    "removed_content": "Removed content",
    "modified_content": "Modified content",
    "missing_expected_field": "Missing expected field",
    "identifier_mismatch": "Identifier mismatch",
    "potential_warning_discrepancy": "Potential warning discrepancy",
    "potential_artwork_discrepancy": "Potential artwork discrepancy",
    "consistency_issue": "Internal consistency",
    "text_not_readable": "Text not readable",
}

RESULT_FLAGGED = "flagged"  # a rule found a discrepancy
RESULT_NOT_EVALUATED = "not_evaluated"  # a rule could not be checked; the reviewer must check manually

BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class Evidence:
    """One piece of supporting material for a finding.

    kind: "text" (a quote from the label), "absence" (nothing was found), "region" (an image
    area), or "measurement" (a computed value such as a page count or decoded barcode).
    """

    source: str  # "approved", "revised" or "both"
    page: int | None
    text: str
    bbox: BBox | None = None
    kind: str = "text"

    @property
    def complete(self) -> bool:
        """Quotes and regions need a page and content; absences and measurements need only a statement."""
        if not self.text.strip():
            return False
        return self.page is not None if self.kind in ("text", "region") else True


@dataclass(frozen=True)
class Finding:
    id: str
    rule_id: str
    rule_name: str
    category: str
    severity: str
    result: str  # RESULT_FLAGGED or RESULT_NOT_EVALUATED
    title: str
    description: str
    field: str  # a field key, "text", "page_count", "barcode" or "pageN"
    approved_value: str | None = None
    revised_value: str | None = None
    evidence: tuple[Evidence, ...] = ()
    recommended_action: str = ""

    @property
    def category_label(self) -> str:
        return CATEGORIES.get(self.category, self.category)

    @property
    def severity_label(self) -> str:
        return SEVERITY_LABELS.get(self.severity, self.severity)

    @property
    def pages(self) -> tuple[int, ...]:
        return tuple(sorted({item.page for item in self.evidence if item.page is not None}))

    @property
    def evidence_complete(self) -> bool:
        """False if there is no evidence at all, or any item lacks its page or content."""
        return bool(self.evidence) and all(item.complete for item in self.evidence)


def make_finding_id(rule_id: str, field_key: str, approved: str | None, revised: str | None, pages: tuple[int, ...]) -> str:
    """A stable id, so the same discrepancy keeps the same id when the same files are analysed again."""
    raw = "|".join([rule_id, field_key, approved or "", revised or "", ",".join(map(str, pages))])
    return f"{rule_id}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:8]}"


@dataclass(frozen=True)
class Rule:
    """A configured check. Defined in rules.toml; see that file for the meaning of each setting."""

    id: str
    name: str
    description: str
    type: str
    severity: str
    category: str | None
    action: str
    enabled: bool = True
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def condition(self) -> str:
        """A readable statement of what the rule checks."""
        p = self.params
        sides = p.get("applies_to", "both")
        text = {
            "required_field": f"Field '{p.get('field')}' must be detected on the {sides} label(s)",
            "field_not_empty": f"Keywords for {', '.join(p.get('fields', []))} must not be printed without a value ({sides})",
            "field_change": f"Field '{p.get('field')}' has a value that is {' / '.join(p.get('change_kinds', []))} between versions",
            "text_change": f"Text not covered by a field finding is {p.get('kind')} between versions",
            "page_count_changed": "The two labels have a different number of pages",
            "udi_check_digit": f"The GTIN check digit in the UDI text must verify ({sides})",
            "date_order": "The expiration date must not be before the manufacturing date",
            "barcode_text_match": f"A decoded barcode must contain the printed UDI-DI ({sides})",
            "barcode_changed": "The decoded barcode value differs between versions",
            "visual_difference": "A page differs visually in an area not explained by text changes",
        }
        return text.get(self.type, self.type)


@dataclass(frozen=True)
class RuleOutcome:
    """What happened when one rule was evaluated."""

    rule_id: str
    rule_name: str
    outcome: str  # "passed", "flagged", "not_evaluated" or "skipped"
    finding_count: int = 0
    note: str = ""


@dataclass(frozen=True)
class VerificationReport:
    findings: tuple[Finding, ...]
    outcomes: tuple[RuleOutcome, ...]

    @property
    def flagged(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.result == RESULT_FLAGGED)

    @property
    def not_evaluated(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.result == RESULT_NOT_EVALUATED)

    def severity_counts(self, flagged_only: bool = True) -> dict[str, int]:
        pool = self.flagged if flagged_only else self.findings
        return {severity: sum(1 for f in pool if f.severity == severity) for severity in SEVERITIES}

    def outcome_counts(self) -> dict[str, int]:
        counts = {"passed": 0, "flagged": 0, "not_evaluated": 0, "skipped": 0}
        for item in self.outcomes:
            counts[item.outcome] = counts.get(item.outcome, 0) + 1
        return counts

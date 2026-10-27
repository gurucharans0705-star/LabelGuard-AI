"""The deterministic explanation: always available, uses no model and sends nothing anywhere.

It restates each finding from the rule's description, the recorded values and the evidence
references, so it has the same shape as an AI-assisted explanation and can be shown in its place.
"""
from __future__ import annotations

from typing import Sequence

from labelguard.ai.models import DETERMINISTIC, Explanation, ExplanationItem, FindingInput
from labelguard.rules.models import SEVERITY_LABELS, SEVERITIES

_QUESTIONS = {
    "identifier_mismatch": "Does the changed identifier match the product master data and an approved change request?",
    "potential_warning_discrepancy": "Was this change to a safety statement intended, and who has confirmed it?",
    "missing_expected_field": "Is the missing or empty field intentional, or was it left out by mistake?",
    "potential_artwork_discrepancy": "Is this visual change intended, and has the artwork owner confirmed it?",
    "removed_content": "Was this text removed on purpose, and is that covered by an approved change request?",
    "added_content": "Is this added text intended, and has it been reviewed?",
    "modified_content": "Is this change covered by an approved change request?",
    "consistency_issue": "Which of the two values is correct according to the source records?",
    "text_not_readable": "Can a text-based version of this label be provided so it can be compared?",
}
_DEFAULT_QUESTION = "What is the reason for this change, and is it covered by an approved change request?"


def _value_sentence(f: FindingInput) -> str:
    parts = []
    if f.approved_value:
        parts.append(f"approved label: \"{f.approved_value}\"")
    if f.revised_value:
        parts.append(f"revised label: \"{f.revised_value}\"")
    return ("Recorded values - " + "; ".join(parts) + ".") if parts else ""


def explain_finding(f: FindingInput) -> ExplanationItem:
    if f.result == "flagged":
        head = f"Rule {f.rule_id} ({f.rule_name}) flagged this: {f.description}"
    else:
        head = (f"Rule {f.rule_id} ({f.rule_name}) could not verify this, which is not a pass: {f.description}")
    pieces = [head.rstrip(), _value_sentence(f)]
    if f.recommended_action:
        pieces.append(f"Recommended reviewer action: {f.recommended_action}")
    if not f.evidence:
        pieces.append("No evidence quote was recorded for this finding.")
    text = " ".join(piece for piece in pieces if piece)
    return ExplanationItem(f.finding_id, text, f.refs, DETERMINISTIC)


def build_summary(inputs: Sequence[FindingInput], omitted: int = 0) -> str:
    flagged = [f for f in inputs if f.result == "flagged"]
    unverified = [f for f in inputs if f.result != "flagged"]
    if not inputs:
        return "The rules found no differences between the two labels. This is not a statement that either label is acceptable."
    counts = [f"{sum(1 for f in flagged if f.severity == s)} {SEVERITY_LABELS[s].lower()}"
              for s in SEVERITIES if any(f.severity == s for f in flagged)]
    text = f"{len(flagged)} discrepancy(ies) were flagged by the configured rules"
    text += f" ({', '.join(counts)})." if counts else "."
    if unverified:
        text += f" {len(unverified)} item(s) could not be verified and need a manual check."
    top = [f.title for f in flagged[:3]]
    if top:
        text += " Most severe: " + "; ".join(top) + "."
    if omitted:
        text += f" {omitted} further finding(s) are not covered here."
    text += " These are differences found by rules, not a compliance determination; a qualified reviewer decides."
    return text


def build_questions(inputs: Sequence[FindingInput]) -> tuple[str, ...]:
    questions = [_QUESTIONS.get(f.category, _DEFAULT_QUESTION) for f in inputs]
    return tuple(dict.fromkeys(questions))[:6]


def deterministic_explanation(inputs: Sequence[FindingInput], omitted: int = 0, note: str = "") -> Explanation:
    return Explanation(
        source=DETERMINISTIC, summary=build_summary(inputs, omitted),
        items=tuple(explain_finding(f) for f in inputs), questions=build_questions(inputs),
        note=note, finding_ids=tuple(f.finding_id for f in inputs),
    )

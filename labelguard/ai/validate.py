"""Validate the model's answer BEFORE anything is shown.

A response is accepted only if it is well-formed JSON of the expected shape, refers only to the
findings and evidence it was given, introduces no identifier, number or quotation that is not in
the input, makes no compliance or approval claim, and cites no regulation that is not in the input.
Any violation rejects the whole response (a partly trusted answer is not shown), and the caller
falls back to the deterministic explanation.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Sequence

from labelguard.ai.models import AiResponseError, FindingInput

MAX_SUMMARY = 900
MAX_EXPLANATION = 700
MAX_QUESTION = 300
MAX_QUESTIONS = 6

_TOP_KEYS = {"summary", "findings", "reviewer_questions"}
_ITEM_KEYS = {"finding_id", "explanation", "evidence_refs"}

# Claims the model must not make. A pattern is ignored if the same words appear in the input itself
# (for example a label that really says "patient"), because then it is quoting, not inventing.
_UNSAFE = [
    (r"\bcompl(?:y|ies|ied|iant|iance)\b|\bnon-?complian\w*", "makes a compliance statement"),
    (r"\bconform(?:s|ing|ity)?\b", "makes a conformity statement"),
    (r"\b(?:approve|approved|approves|approval)\s+(?:for|to|this|the label|release)|\bcan be approved\b|\bshould be approved\b"
     r"|\brecommend\w*\s+(?:approv|releas)\w*", "recommends approval"),
    (r"\bready (?:for|to) (?:release|use|print)\b|\bsafe to (?:release|use|print)\b|\bcleared for\b|\breleas(?:e|ed|ing) (?:the|this) label\b",
     "recommends release"),
    (r"\b(?:is|are|be|remains?) (?:safe|unsafe|acceptable|adequate|sufficient|fine)\b|\bno (?:risk|safety concern)\w*\b",
     "makes a safety or acceptability claim"),
    (r"\bpatient[s']?\b|\bharm\w*\b|\binjur\w*\b|\bhazard\w*\b|\bdanger\w*\b|\bdeath\b|\bfatal\w*\b",
     "makes a claim about patient safety or harm"),
]
_STRONG = 5  # the first five patterns above are never excused by the input
# Regulatory citations. Allowed only when the exact text is in the input.
_CITATION = re.compile(
    r"\b(?:21\s*CFR(?:\s*(?:Part\s*)?\d+)?|CFR\s*Part\s*\d+|ISO\s*\d{3,}(?::\d{4})?|IEC\s*\d{3,}|EU\s*(?:MDR|IVDR)|MDR|IVDR|MDD|"
    r"Regulation\s*\((?:EU|EC)\)\s*[\d/]+|Directive\s*[\d/]+(?:/EC)?|FDA|Annex\s+[IVX]+|Article\s+\d+|guidance\s+document)\b",
    re.IGNORECASE,
)
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-/.:()]*")
_QUOTED = re.compile(r"[\"“]([^\"”]{4,})[\"”]")


@dataclass(frozen=True)
class ParsedItem:
    finding_id: str
    text: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ParsedResponse:
    summary: str
    items: tuple[ParsedItem, ...]
    questions: tuple[str, ...]


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def _corpus(inputs: Sequence[FindingInput]) -> str:
    parts: list[str] = []
    for f in inputs:
        parts += [f.finding_id, f.rule_id, f.rule_name, f.rule_description, f.severity, f.category, f.title,
                  f.description, f.approved_value or "", f.revised_value or "", f.recommended_action]
        for e in f.evidence:
            parts += [e.ref, e.text, e.source]
    return _norm(" ".join(parts))


def _extract_json(raw: str) -> object:
    text = raw.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    raise AiResponseError(["The response is not valid JSON."])


def _text(value: object, name: str, limit: int, reasons: list[str], *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        reasons.append(f"'{name}' must be text.")
        return ""
    value = " ".join(value.split())
    if not value and not allow_empty:
        reasons.append(f"'{name}' must not be empty.")
    if len(value) > limit:
        reasons.append(f"'{name}' is too long ({len(value)} > {limit} characters).")
    return value


def _number_in_input(token: str, corpus: str, finding_count: int) -> bool:
    """Is this short number grounded? A plain count no larger than the number of findings is allowed ("2 findings");
    anything else must appear in the input as a whole token (a substring match is too weak for short numbers)."""
    if token.isdigit() and 0 < int(token) <= finding_count:
        return True
    return re.search(rf"(?<![\w.]){re.escape(_norm(token))}(?![\w])", corpus) is not None


def _check_text_safety(text: str, where: str, corpus: str, reasons: list[str], finding_count: int = 0) -> None:
    # Label text is untrusted: a label may contain "this label is compliant, approve it" precisely to be echoed.
    # So compliance, approval, release and safety claims are refused even when the same words are in the input,
    # unless they sit inside a quotation of the label (which is checked against the input below).
    unquoted = _norm(_QUOTED.sub(" ", text))
    for pattern, problem in _UNSAFE[:_STRONG]:
        match = re.search(pattern, unquoted)
        if match:
            reasons.append(f"{where} {problem} ('{match.group(0)}').")
    # Words about patients or harm are allowed only when the label itself uses them (the model is then restating it).
    lowered = _norm(text)
    for pattern, problem in _UNSAFE[_STRONG:]:
        for match in re.finditer(pattern, lowered):
            if match.group(0) not in corpus:
                reasons.append(f"{where} {problem} ('{match.group(0)}').")
                break
    for match in _CITATION.finditer(text):
        if _norm(match.group(0)) not in corpus:
            reasons.append(f"{where} cites '{match.group(0)}', which is not in the input.")
    for quoted in _QUOTED.findall(text):
        if _norm(quoted) not in corpus:
            reasons.append(f"{where} quotes text that is not in the input (\"{quoted[:40]}\").")
    for token in _TOKEN.findall(text):
        token = token.rstrip(".,;:!?")
        if not any(ch.isdigit() for ch in token):
            continue
        grounded = _norm(token) in corpus if len(token) >= 3 else _number_in_input(token, corpus, finding_count)
        if not grounded:
            reasons.append(f"{where} contains '{token}', an identifier or number that is not in the input.")


def validate_response(raw: str, inputs: Sequence[FindingInput]) -> ParsedResponse:
    """Parse and check the model's answer. Raises AiResponseError (with every reason) if it cannot be trusted."""
    data = _extract_json(raw)
    reasons: list[str] = []
    if not isinstance(data, dict):
        raise AiResponseError(["The response must be a JSON object."])
    for key in sorted(set(data) - _TOP_KEYS):
        reasons.append(f"Unexpected field '{key}'.")
    for key in sorted(_TOP_KEYS - set(data)):
        reasons.append(f"Missing field '{key}'.")
    if reasons:
        raise AiResponseError(reasons)

    by_id = {f.finding_id: f for f in inputs}
    corpus = _corpus(inputs)
    summary = _text(data["summary"], "summary", MAX_SUMMARY, reasons)

    items: list[ParsedItem] = []
    entries = data["findings"]
    if not isinstance(entries, list):
        reasons.append("'findings' must be a list.")
        entries = []
    if len(entries) > len(inputs):
        reasons.append("More explanations than findings were given.")
    seen: set[str] = set()
    for number, entry in enumerate(entries, start=1):
        where = f"Explanation #{number}"
        if not isinstance(entry, dict):
            reasons.append(f"{where} must be an object.")
            continue
        for key in sorted(set(entry) - _ITEM_KEYS):
            reasons.append(f"{where} has an unexpected field '{key}'.")
        finding_id = entry.get("finding_id")
        if not isinstance(finding_id, str) or finding_id not in by_id:
            reasons.append(f"{where} refers to a finding that was not in the input ({str(finding_id)[:40]}).")
            continue
        if finding_id in seen:
            reasons.append(f"{where}: the finding {finding_id} is explained twice.")
        seen.add(finding_id)
        text = _text(entry.get("explanation"), f"{where} explanation", MAX_EXPLANATION, reasons)
        refs = entry.get("evidence_refs")
        if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
            reasons.append(f"{where}: 'evidence_refs' must be a list of text values.")
            refs = []
        allowed = set(by_id[finding_id].refs)
        for ref in refs:
            if ref not in allowed:
                reasons.append(f"{where} cites evidence '{ref[:50]}' that does not belong to finding {finding_id}.")
        if allowed and not [r for r in refs if r in allowed]:
            reasons.append(f"{where} is not grounded: it cites none of the evidence recorded for {finding_id}.")
        _check_text_safety(text, where, corpus, reasons, len(inputs))
        items.append(ParsedItem(finding_id, text, tuple(dict.fromkeys(r for r in refs if r in allowed))))

    questions_raw = data["reviewer_questions"]
    questions: list[str] = []
    if not isinstance(questions_raw, list) or not all(isinstance(q, str) for q in questions_raw):
        reasons.append("'reviewer_questions' must be a list of text values.")
    else:
        if len(questions_raw) > MAX_QUESTIONS:
            reasons.append(f"Too many reviewer questions ({len(questions_raw)} > {MAX_QUESTIONS}).")
        for number, question in enumerate(questions_raw, start=1):
            questions.append(_text(question, f"Question #{number}", MAX_QUESTION, reasons))
            _check_text_safety(question, f"Question #{number}", corpus, reasons, len(inputs))

    _check_text_safety(summary, "The summary", corpus, reasons, len(inputs))
    if reasons:
        raise AiResponseError(reasons)
    return ParsedResponse(summary, tuple(items), tuple(q for q in questions if q))

"""The rule engine: turn comparison results into findings with evidence.

Everything here is deterministic. A finding states what differs or what could not be verified,
quotes the label text it rests on, and says what a reviewer should do. Nothing here interprets
regulations or decides compliance, and nothing a language model writes can change a result.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Callable, Sequence

from labelguard import config
from labelguard.comparison.engine import comparison_key, inline_diff
from labelguard.comparison.models import ComparisonResult, TextChange
from labelguard.extraction.pdf_text import ExtractedDocument
from labelguard.fields.models import FIELD_LABELS, MULTI_VALUED, FieldHit, LabelFields
from labelguard.rules.models import (
    RESULT_FLAGGED,
    RESULT_NOT_EVALUATED,
    SEVERITIES,
    Evidence,
    Finding,
    Rule,
    RuleOutcome,
    VerificationReport,
    make_finding_id,
)
from labelguard.validation.identifiers import (
    compare_dates,
    extract_gtin,
    gtin_check_digit,
    gtin_is_valid,
    parse_label_date,
)

if TYPE_CHECKING:
    from labelguard.vision.barcode import BarcodeScan
    from labelguard.vision.compare import VisualComparison

logger = logging.getLogger(__name__)


@dataclass
class RuleContext:
    """Everything the rules may look at."""

    approved_fields: LabelFields
    revised_fields: LabelFields
    approved_doc: ExtractedDocument
    revised_doc: ExtractedDocument
    comparison: ComparisonResult
    visual: "VisualComparison | None" = None
    approved_barcodes: "BarcodeScan | None" = None
    revised_barcodes: "BarcodeScan | None" = None
    # Processing warnings per side (e.g. "OCR could not run: ..."), quoted when text could not be read.
    read_warnings: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def fields(self, side: str) -> LabelFields:
        return self.approved_fields if side == "approved" else self.revised_fields

    def document(self, side: str) -> ExtractedDocument:
        return self.approved_doc if side == "approved" else self.revised_doc

    def barcodes(self, side: str) -> "BarcodeScan | None":
        return self.approved_barcodes if side == "approved" else self.revised_barcodes


@dataclass
class _State:
    """What earlier rules already reported, so later rules do not report the same thing twice."""

    covered_missing: set[tuple[str, str]] = field(default_factory=set)  # (field, side) reported as not detected
    covered_empty: set[tuple[str, str]] = field(default_factory=set)  # (field, side) reported as empty
    covered_texts: set[str] = field(default_factory=set)  # comparison keys of label text already quoted


@dataclass
class _Result:
    findings: list[Finding] = field(default_factory=list)
    note: str = ""
    outcome: str | None = None  # None: derive from the findings


def _sides(rule: Rule) -> tuple[str, ...]:
    applies = rule.params.get("applies_to", "both")
    return ("approved", "revised") if applies == "both" else (applies,)


def _hit_evidence(side: str, hit: FieldHit) -> Evidence:
    return Evidence(side, hit.page, hit.evidence or "", hit.bbox)


def _make(rule: Rule, *, field_key: str, title: str, description: str, approved: str | None = None,
          revised: str | None = None, evidence: Sequence[Evidence] = (), result: str = RESULT_FLAGGED,
          category: str | None = None) -> Finding:
    """Build a finding. Findings that could not be evaluated are always informational."""
    pages = tuple(sorted({item.page for item in evidence if item.page is not None}))
    return Finding(
        id=make_finding_id(rule.id, f"{field_key}|{title}", approved, revised, pages),
        rule_id=rule.id,
        rule_name=rule.name,
        category=category or rule.category or "modified_content",
        severity=rule.severity if result == RESULT_FLAGGED else "informational",
        result=result,
        title=title,
        description=description,
        field=field_key,
        approved_value=approved,
        revised_value=revised,
        evidence=tuple(evidence),
        recommended_action=rule.action,
    )


# --- Single-label rules ----------------------------------------------------------------------
def _field_not_empty(rule: Rule, ctx: RuleContext, state: _State) -> _Result:
    result = _Result()
    for side in _sides(rule):
        labels = ctx.fields(side)
        for key in rule.params["fields"]:
            hits = labels.empty.get(key, ())
            if not hits or labels.get(key).detected:
                continue
            state.covered_empty.add((key, side))
            evidence = [_hit_evidence(side, hit) for hit in hits]
            approved_value = None
            if side == "revised" and ctx.approved_fields.get(key).detected:
                approved = ctx.approved_fields.get(key)
                approved_value = "; ".join(approved.values)
                evidence += [_hit_evidence("approved", hit) for hit in approved.hits]
            result.findings.append(_make(
                rule, field_key=key,
                title=f"{FIELD_LABELS[key]}: keyword printed without a value ({side} label)",
                description=(
                    f"The {side} label shows the keyword for {FIELD_LABELS[key].lower()} but no value follows it"
                    + (f", although the approved label has \"{approved_value}\"" if approved_value else "")
                    + "."
                ),
                approved=approved_value, evidence=evidence,
            ))
    return result


def _required_field(rule: Rule, ctx: RuleContext, state: _State) -> _Result:
    result = _Result()
    key = rule.params["field"]
    label = FIELD_LABELS[key]
    for side in _sides(rule):
        if ctx.fields(side).get(key).detected or (key, side) in state.covered_empty:
            continue
        pages = ctx.document(side).page_count
        evidence = [Evidence(side, None, f"No {label.lower()} was found in the text of the {side} label ({pages} page(s)).",
                             kind="absence")]
        approved_value = None
        if side == "revised" and ctx.approved_fields.get(key).detected:
            approved = ctx.approved_fields.get(key)
            approved_value = "; ".join(approved.values)
            evidence += [_hit_evidence("approved", hit) for hit in approved.hits]
        state.covered_missing.add((key, side))
        result.findings.append(_make(
            rule, field_key=key,
            title=f"{label}: not detected on the {side} label",
            description=(
                f"No {label.lower()} was detected on the {side} label"
                + (", although the approved label has one" if approved_value else "")
                + ". It may be absent, worded unusually, or unreadable (for example on a poor scan); "
                "a person needs to look at the label itself."
            ),
            approved=approved_value, evidence=evidence,
        ))
    return result


_DEFAULT_CATEGORY = {"modified": "modified_content", "added": "added_content", "removed": "removed_content"}


def _word_changes(before: str, after: str) -> str:
    segments = inline_diff(before, after)
    removed = " ".join(text for kind, text in segments if kind == "removed")
    added = " ".join(text for kind, text in segments if kind == "added")
    parts = []
    if removed:
        parts.append(f"Words only in the approved label: \"{removed}\".")
    if added:
        parts.append(f"Words only in the revised label: \"{added}\".")
    return " ".join(parts)


def _field_change(rule: Rule, ctx: RuleContext, state: _State) -> _Result:
    key = rule.params["field"]
    kinds = set(rule.params["change_kinds"])
    label = FIELD_LABELS[key]
    comparison = next(item for item in ctx.comparison.fields if item.key == key)
    if comparison.status == "not_detected":
        return _Result(note=f"{label} was not detected on either label, so there is nothing to compare.",
                       outcome=RESULT_NOT_EVALUATED)
    multi = key in MULTI_VALUED
    result = _Result()
    skipped = 0
    for change in comparison.changes:
        if change.kind not in kinds:
            continue
        # A single value that vanished or appeared is already reported as "not detected" (or as an
        # empty keyword) by an earlier rule, so it is not reported a second time here.
        if not multi and change.kind == "removed" and (
            (key, "revised") in state.covered_missing or (key, "revised") in state.covered_empty
        ):
            skipped += 1
            continue
        if not multi and change.kind == "added" and (
            (key, "approved") in state.covered_missing or (key, "approved") in state.covered_empty
        ):
            skipped += 1
            continue
        a, b = change.approved, change.revised
        before, after = (a.value if a else None), (b.value if b else None)
        evidence = ([_hit_evidence("approved", a)] if a else []) + ([_hit_evidence("revised", b)] if b else [])
        if change.kind == "modified":
            title = f"{label}: statement reworded" if multi else f"{label} differs between versions"
            description = f"Approved: \"{before}\". Revised: \"{after}\". " + _word_changes(before or "", after or "")
        elif change.kind == "added":
            title = f"{label}: statement added in the revised label" if multi else f"{label} present only in the revised label"
            description = f"The revised label has \"{after}\", which was not found on the approved label."
        else:
            title = f"{label}: statement missing from the revised label" if multi else f"{label} missing from the revised label"
            description = (
                f"The approved label has \"{before}\", which was not found on the revised label. It may have been removed, "
                "reworded beyond recognition, or moved; check the revised label."
            )
        result.findings.append(_make(
            rule, field_key=key, title=title, description=description.strip(), approved=before, revised=after,
            evidence=evidence, category=rule.category or _DEFAULT_CATEGORY[change.kind],
        ))
    if skipped:
        result.note = f"{skipped} change(s) are reported by a required-field rule instead."
    return result


def _udi_check_digit(rule: Rule, ctx: RuleContext, state: _State) -> _Result:
    result = _Result()
    checked = 0
    for side in _sides(rule):
        for hit in ctx.fields(side).get("udi").hits:
            gtin = extract_gtin(hit.value)
            valid = gtin_is_valid(gtin) if gtin else None
            if valid is None:
                continue
            checked += 1
            if not valid:
                expected = gtin_check_digit(gtin[:-1])
                result.findings.append(_make(
                    rule, field_key="udi",
                    title=f"UDI check digit does not verify ({side} label)",
                    description=(
                        f"The GTIN {gtin} ends in check digit {gtin[-1]}, but its first {len(gtin) - 1} digits give {expected}. "
                        "This checks the number's internal consistency only; it does not show whether the identifier is "
                        "registered anywhere."
                    ),
                    approved=hit.value if side == "approved" else None,
                    revised=hit.value if side == "revised" else None,
                    evidence=[_hit_evidence(side, hit)],
                ))
    if not checked:
        result.note = "No GTIN could be read from the printed UDI text."
        result.outcome = RESULT_NOT_EVALUATED
    return result


def _date_order(rule: Rule, ctx: RuleContext, state: _State) -> _Result:
    result = _Result()
    evaluated = 0
    for side in ("approved", "revised"):
        fields = ctx.fields(side)
        expiry, made = fields.get("expiration_date"), fields.get("manufacturing_date")
        if not expiry.detected or not made.detected:
            continue
        expiry_date, made_date = parse_label_date(expiry.value), parse_label_date(made.value)
        if expiry_date is None or made_date is None:
            continue
        outcome = compare_dates(expiry_date, made_date)
        if outcome is None:
            continue
        evaluated += 1
        if outcome < 0:
            result.findings.append(_make(
                rule, field_key="expiration_date",
                title=f"Expiration date is earlier than the manufacturing date ({side} label)",
                description=f"The {side} label shows manufacturing date {made.value} and expiration date {expiry.value}.",
                approved=f"MFG {made.value}; EXP {expiry.value}" if side == "approved" else None,
                revised=f"MFG {made.value}; EXP {expiry.value}" if side == "revised" else None,
                evidence=[_hit_evidence(side, made.hits[0]), _hit_evidence(side, expiry.hits[0])],
            ))
    if not evaluated:
        result.note = "Both dates must be detected and unambiguous (for example 2029-06-30) to compare them."
        result.outcome = RESULT_NOT_EVALUATED
    return result


# --- Barcodes ---------------------------------------------------------------------------------------
def _barcode_not_evaluated(rule: Rule, side: str, scan: "BarcodeScan | None", printed: bool) -> Finding:
    reason = scan.message if scan is not None else "Barcode decoding was not run."
    return _make(
        rule, field_key="barcode", result=RESULT_NOT_EVALUATED,
        title=f"Barcode not verified ({side} label)",
        description=(
            f"{reason} The barcode contents were therefore not checked against the "
            f"{'printed UDI' if printed else 'label'}; this is not a pass."
        ),
        evidence=[Evidence(side, None, reason, kind="absence")],
    )


def _barcode_text_match(rule: Rule, ctx: RuleContext, state: _State) -> _Result:
    result = _Result()
    verified = 0
    for side in _sides(rule):
        printed_hits = ctx.fields(side).get("udi").hits
        scan = ctx.barcodes(side)
        gtins = [gtin for gtin in (extract_gtin(hit.value) for hit in printed_hits) if gtin]
        if scan is None or not scan.decoded:
            if gtins:  # a UDI is printed but its barcode could not be read
                result.findings.append(_barcode_not_evaluated(rule, side, scan, printed=True))
            continue
        if not gtins:
            continue
        match = next((code for code in scan.items if any(gtin in code.digits for gtin in gtins)), None)
        verified += 1
        if match is None:
            decoded = "; ".join(f"{code.text} (page {code.page})" for code in scan.items)
            result.findings.append(_make(
                rule, field_key="barcode",
                title=f"Barcode does not contain the printed UDI-DI ({side} label)",
                description=(
                    f"A barcode was decoded on the {side} label, but none of the decoded values contains the printed "
                    f"device identifier ({', '.join(gtins)}). Decoded: {decoded}."
                ),
                approved=f"printed {printed_hits[0].value}; barcode {decoded}" if side == "approved" else None,
                revised=f"printed {printed_hits[0].value}; barcode {decoded}" if side == "revised" else None,
                evidence=[_hit_evidence(side, printed_hits[0])] + [
                    Evidence(side, code.page, f"Decoded {code.format}: {code.text}", code.bbox, kind="measurement")
                    for code in scan.items
                ],
            ))
    if not verified and not result.findings:
        result.note = "No barcode was decoded, so nothing could be checked."
        result.outcome = RESULT_NOT_EVALUATED
    return result


def _barcode_changed(rule: Rule, ctx: RuleContext, state: _State) -> _Result:
    old, new = ctx.approved_barcodes, ctx.revised_barcodes
    if old is None or new is None or not old.decoded or not new.decoded:
        return _Result(note="A barcode must be decoded on both labels to compare them.", outcome=RESULT_NOT_EVALUATED)
    before = {code.digits or code.text: code for code in old.items}
    after = {code.digits or code.text: code for code in new.items}
    if set(before) == set(after):
        return _Result()
    describe = lambda items: "; ".join(f"{code.text} ({code.format}, page {code.page})" for code in items.values())  # noqa: E731
    evidence = [Evidence("approved", code.page, f"Decoded {code.format}: {code.text}", code.bbox, kind="measurement")
                for code in old.items]
    evidence += [Evidence("revised", code.page, f"Decoded {code.format}: {code.text}", code.bbox, kind="measurement")
                 for code in new.items]
    return _Result([_make(
        rule, field_key="barcode",
        title="Decoded barcode value differs between versions",
        description=f"Approved label barcode: {describe(before)}. Revised label barcode: {describe(after)}.",
        approved=describe(before), revised=describe(after), evidence=evidence,
    )])


# --- Document-level rules ------------------------------------------------------------------------
def _page_count_changed(rule: Rule, ctx: RuleContext, state: _State) -> _Result:
    old, new = ctx.approved_doc.page_count, ctx.revised_doc.page_count
    if old == new:
        return _Result()
    return _Result([_make(
        rule, field_key="page_count",
        title=f"Page count differs: {old} page(s) approved, {new} page(s) revised",
        description="The documents have a different number of pages. Visual comparison only covers pages that exist in both.",
        approved=str(old), revised=str(new),
        evidence=[Evidence("both", None, f"Approved label: {old} page(s). Revised label: {new} page(s).", kind="measurement")],
    )])


def _is_covered(key: str, covered: set[str]) -> bool:
    return any(key == c or (len(c) >= 6 and c in key) or (len(key) >= 6 and key in c) for c in covered)


def _text_change(rule: Rule, ctx: RuleContext, state: _State) -> _Result:
    kind = rule.params["kind"]
    result = _Result()

    def covered(change: TextChange) -> bool:
        return any(
            _is_covered(comparison_key(text), state.covered_texts)
            for text in (change.approved_text, change.revised_text) if text
        )

    changes = ctx.comparison.text_changes
    run: list[TextChange] = []
    previous = -2

    def flush() -> None:
        if not run:
            return
        side = "revised" if kind == "added" else "approved"
        page = run[0].revised_page if kind == "added" else run[0].approved_page
        lines = [(c.revised_text if kind == "added" else c.approved_text) or "" for c in run]
        noun = "added on" if kind == "added" else "missing from"
        result.findings.append(_make(
            rule, field_key="text",
            title=f"{len(run)} line(s) of text {noun} {'revised' if kind == 'added' else 'approved'} page {page}",
            description=(
                "Text that is not part of a field finding "
                + ("appears only on the revised label." if kind == "added" else "is missing from the revised label.")
            ),
            approved=None if kind == "added" else "\n".join(lines),
            revised="\n".join(lines) if kind == "added" else None,
            evidence=[Evidence(side, page, line, c.revised_bbox if kind == "added" else c.approved_bbox)
                      for line, c in zip(lines, run)],
        ))
        run.clear()

    for index, change in enumerate(changes):
        if change.kind != kind or covered(change):
            continue
        if kind == "modified":
            result.findings.append(_make(
                rule, field_key="text",
                title=f"Text reworded on page {change.approved_page or change.revised_page}",
                description=_word_changes(change.approved_text or "", change.revised_text or "") or "The wording differs.",
                approved=change.approved_text, revised=change.revised_text,
                evidence=[Evidence("approved", change.approved_page, change.approved_text or "", change.approved_bbox),
                          Evidence("revised", change.revised_page, change.revised_text or "", change.revised_bbox)],
            ))
            continue
        page = change.revised_page if kind == "added" else change.approved_page
        same_page = run and (run[0].revised_page if kind == "added" else run[0].approved_page) == page
        if run and not (index == previous + 1 and same_page):
            flush()
        run.append(change)
        previous = index
    flush()
    moved = ctx.comparison.moved_lines
    if moved and kind == "modified":
        result.note = f"{moved} line(s) appear in a different order with unchanged wording; not reported as a change."
    return result


def _visual_difference(rule: Rule, ctx: RuleContext, state: _State) -> _Result:
    visual = ctx.visual
    if visual is None:
        return _Result(note="Visual comparison was not run.", outcome="skipped")
    if visual.status != "ok":
        reason = visual.message
        return _Result([_make(
            rule, field_key="artwork", result=RESULT_NOT_EVALUATED,
            title="Visual comparison not performed",
            description=f"{reason} Artwork was therefore not compared; this is not a pass.",
            evidence=[Evidence("both", None, reason, kind="absence")],
        )])
    result = _Result()
    for page in visual.pages:
        where = f"approved page {page.approved_page} / revised page {page.revised_page}"
        if page.status in ("not_comparable", "failed"):
            result.findings.append(_make(
                rule, field_key=f"page{page.approved_page}", result=RESULT_NOT_EVALUATED,
                title=f"Visual comparison not reliable for {where}",
                description=f"{page.message} Check this page's artwork manually; this is not a pass.",
                evidence=[Evidence("both", page.approved_page, page.message, kind="absence")],
            ))
        elif page.status == "differences":
            regions = page.unexplained
            evidence = [
                Evidence("both", page.approved_page,
                         "Region {}: {:.1f}% from the left, {:.1f}% from the top, {:.1f}% wide, {:.1f}% tall (percent of the page)".format(
                             number, *region.as_percent(page.width, page.height)),
                         kind="region")
                for number, region in enumerate(regions, start=1)
            ]
            evidence.append(Evidence(
                "both", page.approved_page,
                f"{page.difference_ratio:.1%} of the page's pixels differ; "
                f"{len(page.regions) - len(regions)} further region(s) coincide with changed text.",
                kind="measurement",
            ))
            note = f" {page.reliability_note}" if page.reliability_note else ""
            result.findings.append(_make(
                rule, field_key=f"page{page.approved_page}",
                title=f"Visual difference on {where}",
                description=(
                    f"{len(regions)} region(s) look different between the two versions and are not explained by detected "
                    f"text changes. This can be intended (a moved logo, a new colour, a regenerated barcode).{note}"
                ),
                evidence=evidence,
            ))
    return result


_EVALUATORS: dict[str, Callable[[Rule, RuleContext, _State], _Result]] = {
    "field_not_empty": _field_not_empty,
    "required_field": _required_field,
    "field_change": _field_change,
    "udi_check_digit": _udi_check_digit,
    "date_order": _date_order,
    "barcode_text_match": _barcode_text_match,
    "barcode_changed": _barcode_changed,
    "page_count_changed": _page_count_changed,
    "text_change": _text_change,  # after field rules: it skips text those already reported
    "visual_difference": _visual_difference,
}
_ORDER = {name: position for position, name in enumerate(_EVALUATORS)}

# --- Text readability ----------------------------------------------------------------------------
# A page that is only an image, where OCR did not run or found nothing, has no text. That is "we could
# not read it", not "the label says nothing", so rules that depend on that side's text must not run:
# they would report every field and warning as missing. This check is built into the engine rather than
# configured in rules.toml, because switching it off would bring those false findings back.
READABILITY_RULE = Rule(
    id="R-READ-001",
    name="Label text could not be read reliably",
    description="A page has no readable text (an image-only page that OCR did not read), or OCR was uncertain.",
    type="text_readability",
    severity="informational",
    category="text_not_readable",
    action="Provide a text-based PDF or enable OCR, or compare these pages manually. Nothing was concluded from them.",
)
_BOTH_SIDES_TEXT_RULES = {"field_change", "text_change"}  # compare the text of both labels
_ONE_SIDE_TEXT_RULES = {"required_field", "field_not_empty", "udi_check_digit", "barcode_text_match"}


def _unreadable_pages(doc: ExtractedDocument) -> list[int]:
    return doc.unreadable_pages


def _fully_unreadable_sides(ctx: RuleContext) -> dict[str, list[int]]:
    """Sides on which every page is unreadable (nothing at all can be compared)."""
    sides = {}
    for side in ("approved", "revised"):
        doc = ctx.document(side)
        bad = _unreadable_pages(doc)
        if doc.page_count and len(bad) == doc.page_count:
            sides[side] = bad
    return sides


def _readability_findings(ctx: RuleContext, fully: dict[str, list[int]]) -> list[Finding]:
    rule, findings = READABILITY_RULE, []
    for side in ("approved", "revised"):
        doc = ctx.document(side)
        bad = _unreadable_pages(doc)
        reasons = " ".join(ctx.read_warnings.get(side, ()))
        if bad:
            pages = ", ".join(map(str, bad))
            if side in fully:
                effect = (
                    f"No text could be read from the {side} label, so no text, field or warning comparison was "
                    "made for it. This is not a statement that the label is empty or unchanged."
                )
            else:
                effect = (
                    "Text on those pages was not compared. Fields or warnings that sit only on those pages may "
                    "appear as missing or removed in other findings; check them against the page images."
                )
            findings.append(_make(
                rule, field_key=f"unreadable_{side}", result=RESULT_NOT_EVALUATED,
                title=f"{side.capitalize()} label: text not readable on page(s) {pages}",
                description=f"{effect} {reasons}".strip(),
                evidence=[Evidence(side, page, "Image-only page: no selectable text and no OCR text.", kind="absence")
                          for page in bad],
            ))
        for page in doc.pages:
            if page.source == "ocr" and page.ocr_confidence is not None and page.ocr_confidence < config.OCR_LOW_CONFIDENCE:
                findings.append(_make(
                    rule, field_key=f"low_ocr_{side}_{page.page_number}", result=RESULT_NOT_EVALUATED,
                    title=f"{side.capitalize()} label: OCR uncertain on page {page.page_number}",
                    description=(
                        f"OCR read this page with a mean confidence of {page.ocr_confidence:.0f}% "
                        f"(below {config.OCR_LOW_CONFIDENCE}%). Text differences found here may be misreadings; "
                        "verify them against the page image."
                    ),
                    evidence=[Evidence(side, page.page_number, f"OCR mean confidence {page.ocr_confidence:.0f}%",
                                       kind="measurement")],
                ))
    return findings


def _text_rule_to_run(rule: Rule, fully: dict[str, list[int]]) -> tuple[Rule | None, str]:
    """The rule to evaluate given unreadable sides, or None (with the reason) if it cannot run."""
    if not fully:
        return rule, ""
    names = " and ".join(fully)
    if rule.type in _BOTH_SIDES_TEXT_RULES:
        return None, f"Not evaluated: the text of the {names} label could not be read."
    if rule.type in _ONE_SIDE_TEXT_RULES:
        readable = [side for side in _sides(rule) if side not in fully]
        if not readable:
            return None, f"Not evaluated: the text of the {names} label could not be read."
        if len(readable) < len(_sides(rule)):
            return replace(rule, params={**rule.params, "applies_to": readable[0]}), (
                f"Checked on the {readable[0]} label only; the text of the {names} label could not be read."
            )
    return rule, ""


def evaluate_rules(rules: Sequence[Rule], context: RuleContext) -> VerificationReport:
    """Run every enabled rule and collect findings (most severe first) and per-rule outcomes."""
    state = _State()
    findings: list[Finding] = []
    outcomes: list[RuleOutcome] = []
    seen_ids: set[str] = set()

    fully = _fully_unreadable_sides(context)
    readability = _readability_findings(context, fully)
    findings.extend(readability)
    seen_ids.update(f.id for f in readability)
    outcomes.append(RuleOutcome(
        READABILITY_RULE.id, READABILITY_RULE.name, "not_evaluated" if readability else "passed", len(readability),
        "" if readability else "Every page has readable text.",
    ))

    for original in sorted(rules, key=lambda r: _ORDER.get(r.type, len(_ORDER))):
        if not original.enabled:
            outcomes.append(RuleOutcome(original.id, original.name, "skipped", 0, "Rule is disabled."))
            continue
        rule, reason = _text_rule_to_run(original, fully)
        if rule is None:
            outcomes.append(RuleOutcome(original.id, original.name, "not_evaluated", 0, reason))
            continue
        partial_note = reason  # the rule runs, but on one label only
        try:
            result = _EVALUATORS[rule.type](rule, context, state)
        except Exception as exc:  # one broken rule must not hide the others, and must not look like a pass
            logger.exception("Rule %s could not be evaluated", rule.id)
            reason = f"{exc.__class__.__name__}: {exc}"
            result = _Result([_make(
                rule, field_key="rule", result=RESULT_NOT_EVALUATED,
                title=f"Rule {rule.id} could not be evaluated",
                description=f"The rule failed with an internal error ({reason}). Its check was not performed.",
                evidence=[Evidence("both", None, reason, kind="absence")],
            )])

        fresh = [f for f in result.findings if f.id not in seen_ids]
        seen_ids.update(f.id for f in fresh)
        findings.extend(fresh)
        if rule.type not in ("text_change", "visual_difference"):
            for finding in fresh:
                for item in finding.evidence:
                    if item.kind == "text" and item.text:
                        state.covered_texts.add(comparison_key(item.text))
                for value in (finding.approved_value, finding.revised_value):
                    if value and finding.field in FIELD_LABELS:
                        state.covered_texts.add(comparison_key(value))

        if any(f.result == RESULT_FLAGGED for f in fresh):
            outcome = "flagged"
        elif fresh or result.outcome == RESULT_NOT_EVALUATED:
            outcome = "not_evaluated"
        else:
            outcome = result.outcome or "passed"
        note = result.note
        if partial_note:  # half of the check could not be made, so it must not read as a pass
            note = f"{partial_note} {note}".strip()
            if outcome == "passed":
                outcome = "not_evaluated"
        outcomes.append(RuleOutcome(rule.id, rule.name, outcome, len(fresh), note))

    rank = {severity: position for position, severity in enumerate(SEVERITIES)}
    findings.sort(key=lambda f: (f.result != RESULT_FLAGGED, rank.get(f.severity, len(rank)), f.rule_id, f.field, f.id))
    order = {rule.id: position for position, rule in enumerate(rules)}
    outcomes.sort(key=lambda o: order.get(o.rule_id, len(order)))
    return VerificationReport(tuple(findings), tuple(outcomes))

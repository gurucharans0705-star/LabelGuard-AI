"""Rule-based label field extraction.

Design principles
-----------------
* Evidence first: every detected value stores the label text it came from, plus
  page and position when known.
* No guessing: if a field cannot be found, it is simply "not detected". Values
  that are inferences rather than keyword matches are marked method="heuristic".
* Works on text lines, so it behaves the same for digital PDFs and OCR output
  (OCR often puts several items, such as "REF ... STERILE EO", on one line).
"""
from __future__ import annotations

import re
import statistics

from labelguard.extraction.pdf_text import ExtractedDocument, TextLine
from labelguard.fields.language import detect_languages
from labelguard.fields.models import FIELD_LABELS, ExtractedField, FieldHit, LabelFields

_I = re.IGNORECASE

# --- Identifiers -------------------------------------------------------------
_VALUE = r"(?P<value>[A-Z0-9][A-Z0-9\-_/.]{2,})"
_REF_RE = re.compile(
    r"\b(?:REF|REFERENCE|CAT(?:ALOG(?:UE)?)?\.?(?:\s*(?:NO\.?|NUMBER|#))?|MODEL(?:\s*(?:NO\.?|NUMBER|#))?"
    r"|PART\s*(?:NO\.?|NUMBER|#)|P/N|ITEM\s*(?:NO\.?|NUMBER|#)|PRODUCT\s*CODE|SKU)[\s:#]+" + _VALUE,
    _I,
)
_LOT_RE = re.compile(
    r"\b(?:LOT|BATCH)(?:\s*(?:NO\.?|NUMBER|#|CODE))?(?:\s*[:#]+\s*|\s+)(?P<value>[A-Z0-9][A-Z0-9\-_/.]*)", _I
)

# --- Dates -------------------------------------------------------------------
_MONTH = (
    r"(?:JAN(?:UARY)?|FEB(?:RUARY)?|MAR(?:CH)?|APR(?:IL)?|MAY|JUN(?:E)?|JUL(?:Y)?|AUG(?:UST)?"
    r"|SEP(?:T(?:EMBER)?)?|OCT(?:OBER)?|NOV(?:EMBER)?|DEC(?:EMBER)?)"
)
_DATE = (
    r"(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"  # 2029-06-30
    r"|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}"  # 30/06/2029
    r"|\d{1,2}[-\s]" + _MONTH + r"[-\s,.]*\d{2,4}"  # 30 JUN 2029
    r"|" + _MONTH + r"[-\s.]*\d{1,2}[,\s]+\d{4}"  # JUNE 30, 2029
    r"|" + _MONTH + r"[-\s.,]*\d{4}"  # JUN 2029
    r"|\d{4}[-/.]\d{1,2}"  # 2029-06
    r"|\d{1,2}[-/.]\d{4})(?!\d)"  # 06/2029
)
_EXPIRY_RE = re.compile(
    r"\b(?:EXP(?:IRY|IRATION|IRES)?\.?(?:\s*DATE)?|USE[\s-]+BY|BEST\s+BEFORE)\b\.?\s*[:\-]?\s*(?P<value>" + _DATE + r")",
    _I,
)
_MFG_DATE_RE = re.compile(
    r"\b(?:MFG|MFD|MANUFACTURED|DATE\s+OF\s+MANUFACTURE|MANUFACTURE\s+DATE|PRODUCTION\s+DATE|DOM)\b\.?(?:\s*DATE)?"
    r"\s*[:\-]?\s*(?P<value>" + _DATE + r")",
    _I,
)

# --- UDI ---------------------------------------------------------------------
_GS1_RE = re.compile(r"\(01\)\s*\d{14}(?:\s*\(\d{2,4}\)\s*[A-Za-z0-9._\-]+)*")
_UDI_LABEL_RE = re.compile(r"\bUDI(?:[-\s]?(?:DI|PI))?\s*[:#]\s*(?P<value>\S{6,}(?:\s*\(\d{2,4}\)\s*\S+)*)", _I)
_GTIN_RE = re.compile(r"\bGTIN(?:-\d+)?\s*[:#]?\s*(?P<value>\d{8,14})\b", _I)

# --- Manufacturer and product name -------------------------------------------
_MFR_LABEL_RE = re.compile(r"\b(?:LEGAL\s+MANUFACTURER|MANUFACTURER|MFR\.?)\s*[:\-]\s*(?P<value>.*)$", _I)
_MFR_BY_RE = re.compile(r"\b(?:MANUFACTURED|MADE)\s+BY\b\s*[:\-]?\s*(?P<value>.*)$", _I)
_COMPANY_RE = re.compile(
    r"^(?P<value>[A-Z][\w&'’.\- ]*?\b(?i:Ltd|Limited|Inc|Incorporated|LLC|GmbH|Corp|Corporation|PLC|SAS|B\.?V|N\.?V|Pvt|Pty|AG)\b\.?)"
    r"(?:[,\s]|$)"
)
_PRODUCT_LABEL_RE = re.compile(
    r"\b(?:PRODUCT\s+NAME|PRODUCT|DEVICE\s+NAME|DEVICE|DESCRIPTION)\s*[:\-]\s*(?P<value>\S.*)$", _I
)

# --- Statements: warnings, storage, sterilization ------------------------------
_WARNING_HEADING_RE = re.compile(
    r"^\W*(?:WARNINGS?|CAUTIONS?|PRECAUTIONS?|CONTRAINDICATIONS?|SAFETY\s+INFORMATION)\W*$", _I
)
_WARNING_KEYWORDS_RE = re.compile(
    r"\b(?:WARNING|CAUTION|DO\s+NOT|DON'T|SINGLE[\s-]USE|NOT\s+FOR|KEEP\s+(?:OUT|AWAY)|LATEX|CONTRAINDICAT\w*"
    r"|FEDERAL\s+LAW|RX\s+ONLY|PRESCRIPTION\s+ONLY|DISPOSE|DISCARD|NEVER|MUST\s+NOT|DANGER|HAZARD\w*|FLAMMABLE"
    r"|BIOHAZARD|RADIATION)\b",
    _I,
)
_STORAGE_RE = re.compile(
    r"\b(?:STOR(?:E|ED|AGE)|TEMPERATURE\s+(?:LIMIT|RANGE)|KEEP\s+(?:DRY|COOL|REFRIGERATED)"
    r"|PROTECT\s+FROM\s+(?:LIGHT|MOISTURE|HEAT)|HUMIDITY)\b",
    _I,
)
_STERILE_RE = re.compile(
    r"\b(?:NON[-\s]?STERILE|STERILE(?:\s+(?:EO|R|A|VH2O2|STEAM|ASEPTIC)\b)?|STERILI[SZ](?:ED|ATION)\b[^.\n]*"
    r"|ETHYLENE\s+OXIDE|GAMMA\s+(?:STERILI[SZ]ED|IRRADIAT\w*)|STEAM\s+STERILI[SZ]\w*|AUTOCLAV\w*"
    r"|(?:DO\s+NOT\s+)?RESTERILI[SZ]\w*)",
    _I,
)
_BULLET_RE = re.compile(r"^[\-•–—*▪●]+\s*")
_NEW_ITEM_RE = re.compile(r"^(?:[\-•–—*▪●]\s|\d{1,2}[.)]\s)")
_ALL_CAPS_HEADING_RE = re.compile(r"^[A-Z][A-Z\s/&\-]{3,40}:?$")
_SENTENCE_END = (".", "!", "?", ";")

MAX_SECTION_STATEMENTS = 12


# --- Helpers ------------------------------------------------------------------
class _Collector:
    """Collects hits, ignoring duplicates (same value ignoring case and spacing)."""

    def __init__(self) -> None:
        self.hits: list[FieldHit] = []
        self._seen: set[str] = set()

    def add(self, value: str, evidence: str, page: int | None, bbox, method: str) -> None:
        value = value.strip()
        key = re.sub(r"\s+", "", value).casefold()
        if not value or key in self._seen:
            return
        self._seen.add(key)
        self.hits.append(FieldHit(value, evidence, page, bbox, method))


def _strip_bullet(text: str) -> str:
    return _BULLET_RE.sub("", text).strip()


def _is_field_line(text: str) -> bool:
    """True if the line looks like a labelled field (REF, LOT, EXP, storage, sterile...)."""
    return any(
        regex.search(text)
        for regex in (
            _REF_RE, _LOT_RE, _EXPIRY_RE, _MFG_DATE_RE, _UDI_LABEL_RE, _GS1_RE, _GTIN_RE,
            _MFR_LABEL_RE, _MFR_BY_RE, _PRODUCT_LABEL_RE, _STORAGE_RE, _STERILE_RE,
        )
    )


def _starts_new_item(text: str) -> bool:
    return bool(
        _NEW_ITEM_RE.match(text)
        or _WARNING_HEADING_RE.match(text)
        or _ALL_CAPS_HEADING_RE.match(text)
        or _is_field_line(text)
    )


def _gather_statement(lines: list[TextLine], start: int, first_text: str):
    """Join wrapped continuation lines onto a statement. Returns (text, last_index, bbox)."""
    parts = [first_text]
    end = start
    x0, y0, x1, y1 = lines[start].bbox
    while not parts[-1].rstrip().endswith(_SENTENCE_END) and end + 1 < len(lines):
        current, following = lines[end], lines[end + 1]
        if following.page != current.page or _starts_new_item(following.text):
            break
        height = current.bbox[3] - current.bbox[1]
        gap = following.bbox[1] - current.bbox[3]
        if height > 0 and gap > 0.9 * height:  # a visible vertical gap: a new paragraph
            break
        parts.append(following.text)
        end += 1
        fx0, fy0, fx1, fy1 = following.bbox
        x0, y0, x1, y1 = min(x0, fx0), min(y0, fy0), max(x1, fx1), max(y1, fy1)
    return " ".join(part.strip() for part in parts), end, (x0, y0, x1, y1)


def _find_with_regex(lines: list[TextLine], regex: re.Pattern, *, require_digit: bool = False) -> tuple[FieldHit, ...]:
    collector = _Collector()
    for line in lines:
        for match in regex.finditer(line.text):
            value = match.group("value").strip().rstrip(".-_/,;:")
            if require_digit and not any(char.isdigit() for char in value):
                continue
            collector.add(value, line.text, line.page, line.bbox, "labelled")
    return tuple(collector.hits)


# --- Field finders --------------------------------------------------------------
def _find_udi(lines: list[TextLine]) -> tuple[FieldHit, ...]:
    collector = _Collector()
    for line in lines:
        for match in _GS1_RE.finditer(line.text):
            collector.add(re.sub(r"\s+", "", match.group(0)), line.text, line.page, line.bbox, "pattern")
        for match in _UDI_LABEL_RE.finditer(line.text):
            collector.add(re.sub(r"\s+", "", match.group("value")), line.text, line.page, line.bbox, "labelled")
        for match in _GTIN_RE.finditer(line.text):
            collector.add(match.group("value"), line.text, line.page, line.bbox, "labelled")
    return tuple(collector.hits)


def _find_manufacturer(lines: list[TextLine]) -> tuple[FieldHit, ...]:
    collector = _Collector()
    for index, line in enumerate(lines):
        for regex in (_MFR_LABEL_RE, _MFR_BY_RE):
            match = regex.search(line.text)
            if not match:
                continue
            value = match.group("value").strip(" :;-")
            evidence = line.text
            if not value and index + 1 < len(lines) and lines[index + 1].page == line.page:
                value = lines[index + 1].text.strip()  # name printed on the line below the keyword
                evidence = f"{line.text} / {lines[index + 1].text}"
            collector.add(value, evidence, line.page, line.bbox, "labelled")
            break
    if collector.hits:
        return tuple(collector.hits)

    # No "Manufacturer:" keyword: fall back to company-name patterns (Ltd, Inc, GmbH...).
    for line in lines:
        match = _COMPANY_RE.match(line.text)
        if match and len(collector.hits) < 3:
            collector.add(match.group("value"), line.text, line.page, line.bbox, "pattern")
    return tuple(collector.hits)


def _find_product_name(lines: list[TextLine], manufacturer: tuple[FieldHit, ...]) -> tuple[FieldHit, ...]:
    collector = _Collector()
    for line in lines:
        match = _PRODUCT_LABEL_RE.search(line.text)
        if match:
            collector.add(match.group("value"), line.text, line.page, line.bbox, "labelled")
    if collector.hits or not lines:
        return tuple(collector.hits)

    # No "Product:" keyword. Heuristic: the most prominent text line on the first page that is
    # not a labelled field or the company name. Only accepted if it is clearly larger than
    # the typical text, so plain documents do not produce a made-up product name.
    sizes = [line.font_size for line in lines if line.font_size]
    if not sizes:
        return ()
    typical = statistics.median(sizes)
    manufacturer_text = " ".join(hit.value for hit in manufacturer).casefold()
    first_page = lines[0].page
    candidates = []
    for index, line in enumerate(lines):
        text = line.text.strip()
        if line.page != first_page or not line.font_size:
            continue
        if len(text) < 4 or len(text) > 120 or not any(char.isalpha() for char in text):
            continue
        if _is_field_line(text) or _COMPANY_RE.match(text) or _WARNING_HEADING_RE.match(text):
            continue
        if text.casefold().strip(" .,") in manufacturer_text:
            continue
        candidates.append((line.font_size, -index, line))
    if not candidates:
        return ()
    best = max(candidates, key=lambda item: (item[0], item[1]))[2]
    if best.font_size < typical * 1.1:
        return ()
    collector.add(best.text, best.text, best.page, best.bbox, "heuristic")
    return tuple(collector.hits)


def _find_warnings(lines: list[TextLine]) -> tuple[FieldHit, ...]:
    collector = _Collector()
    in_section = False
    section_count = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        text = _strip_bullet(line.text)
        if _WARNING_HEADING_RE.match(text):
            in_section, section_count = True, 0
            index += 1
            continue
        if in_section and (_is_field_line(text) or _ALL_CAPS_HEADING_RE.match(text) or section_count >= MAX_SECTION_STATEMENTS):
            in_section = False
        if in_section or _WARNING_KEYWORDS_RE.search(text):
            statement, last, bbox = _gather_statement(lines, index, text)
            collector.add(_strip_bullet(statement), statement, line.page, bbox, "warning section" if in_section else "labelled")
            section_count += 1
            index = last + 1
            continue
        index += 1
    return tuple(collector.hits)


def _find_storage(lines: list[TextLine]) -> tuple[FieldHit, ...]:
    collector = _Collector()
    index = 0
    while index < len(lines):
        text = _strip_bullet(lines[index].text)
        if _STORAGE_RE.search(text):
            statement, last, bbox = _gather_statement(lines, index, text)
            collector.add(statement, statement, lines[index].page, bbox, "labelled")
            index = last + 1
        else:
            index += 1
    return tuple(collector.hits)


def _find_sterilization(lines: list[TextLine]) -> tuple[FieldHit, ...]:
    collector = _Collector()
    for line in lines:
        for match in _STERILE_RE.finditer(line.text):
            collector.add(match.group(0).strip(" ,;"), line.text, line.page, line.bbox, "labelled")
    return tuple(collector.hits)


# --- Public API -------------------------------------------------------------------
def extract_fields(document: ExtractedDocument) -> LabelFields:
    """Extract label fields (with evidence) from an extracted document."""
    lines = document.all_lines
    manufacturer = _find_manufacturer(lines)
    found = {
        "product_name": _find_product_name(lines, manufacturer),
        "manufacturer": manufacturer,
        "product_identifier": _find_with_regex(lines, _REF_RE, require_digit=True),
        "udi": _find_udi(lines),
        "lot_number": _find_with_regex(lines, _LOT_RE, require_digit=True),
        "expiration_date": _find_with_regex(lines, _EXPIRY_RE),
        "manufacturing_date": _find_with_regex(lines, _MFG_DATE_RE),
        "safety_warnings": _find_warnings(lines),
        "storage_instructions": _find_storage(lines),
        "sterilization": _find_sterilization(lines),
    }
    fields = {key: ExtractedField(key, found[key]) for key in FIELD_LABELS}
    return LabelFields(fields=fields, languages=detect_languages(document.full_text))

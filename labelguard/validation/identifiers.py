"""Checks on identifier and date *values*: GTIN check digits and label date parsing.

These are format checks only. A valid GTIN check digit says the number is internally
consistent; it does not say the identifier is registered with GS1 or listed in any UDI
database, and nothing here interprets regulations.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass

GTIN_LENGTHS = (8, 12, 13, 14)


def gtin_check_digit(body: str) -> int:
    """GS1 mod-10 check digit for a GTIN body (all digits except the last one)."""
    if not body.isdigit():
        raise ValueError("GTIN body must contain digits only")
    total = sum(int(char) * (3 if position % 2 == 0 else 1) for position, char in enumerate(reversed(body)))
    return (10 - total % 10) % 10


def gtin_is_valid(digits: str) -> bool | None:
    """True/False if `digits` is a GTIN-8/12/13/14 with a correct/incorrect check digit; None if not a GTIN."""
    if not digits.isdigit() or len(digits) not in GTIN_LENGTHS:
        return None
    return gtin_check_digit(digits[:-1]) == int(digits[-1])


_GS1_DI_RE = re.compile(r"\(01\)\s*(\d{14})")


def extract_gtin(udi_text: str) -> str | None:
    """Pull the GTIN out of a printed UDI string such as "(01)08901234567890(17)290630" or "08901234567890"."""
    match = _GS1_DI_RE.search(udi_text)
    if match:
        return match.group(1)
    compact = re.sub(r"[\s\-]", "", udi_text)
    if compact.isdigit() and len(compact) in GTIN_LENGTHS:
        return compact
    return None


# --- Dates ---------------------------------------------------------------------------------
_MONTHS = {name: number for number, name in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_MON = r"(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"


@dataclass(frozen=True)
class LabelDate:
    """A date as printed on a label. `day` is None when only the month is printed."""

    year: int
    month: int
    day: int | None = None

    def key(self) -> tuple[int, int, int]:
        return (self.year, self.month, self.day or 0)


def _year(text: str) -> int:
    value = int(text)
    return value + 2000 if value < 100 else value


def _valid(year: int, month: int, day: int | None) -> LabelDate | None:
    if not 1 <= month <= 12 or not 1900 <= year <= 2200:
        return None
    if day is not None and not 1 <= day <= calendar.monthrange(year, month)[1]:
        return None
    return LabelDate(year, month, day)


def parse_label_date(text: str) -> LabelDate | None:
    """Parse a printed date. Returns None for unreadable or ambiguous dates (such as 03/04/2029).

    Numeric day/month dates are only accepted when the order is unambiguous (one number above 12).
    """
    value = re.sub(r"\s+", " ", text.strip().lower()).strip(" .,")
    if match := re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", value):
        return _valid(int(match[1]), int(match[2]), int(match[3]))
    if match := re.fullmatch(r"(\d{4})[-/.](\d{1,2})", value):
        return _valid(int(match[1]), int(match[2]), None)
    if match := re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", value):
        first, second, year = int(match[1]), int(match[2]), _year(match[3])
        if first > 12 >= second:
            return _valid(year, second, first)
        if second > 12 >= first:
            return _valid(year, first, second)
        return None  # ambiguous: day-first or month-first cannot be told apart
    if match := re.fullmatch(r"(\d{1,2})[-\s]+" + _MON + r"[-\s,.]*(\d{2,4})", value):
        return _valid(_year(match[3]), _MONTHS[match["mon"]], int(match[1]))
    if match := re.fullmatch(_MON + r"[-\s.]*(\d{1,2})[,\s]+(\d{4})", value):
        return _valid(int(match[3]), _MONTHS[match["mon"]], int(match[2]))
    if match := re.fullmatch(_MON + r"[-\s.,]*(\d{4})", value):
        return _valid(int(match[2]), _MONTHS[match["mon"]], None)
    if match := re.fullmatch(r"(\d{1,2})[-/.](\d{4})", value):
        return _valid(int(match[2]), int(match[1]), None)
    return None


def compare_dates(first: LabelDate, second: LabelDate) -> int | None:
    """-1, 0 or 1 if `first` is before, equal to or after `second`; None if the precision cannot decide.

    When one date has no day, only the year and month are compared, and equal months are inconclusive.
    """
    if first.year != second.year or first.month != second.month:
        return -1 if (first.year, first.month) < (second.year, second.month) else 1
    if first.day is None or second.day is None:
        return None
    return (first.day > second.day) - (first.day < second.day)

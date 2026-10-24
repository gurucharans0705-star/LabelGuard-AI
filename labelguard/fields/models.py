"""Data structures for extracted label fields. Every detected value carries its evidence."""
from __future__ import annotations

from dataclasses import dataclass, field

from labelguard.fields.language import LanguageGuess

FIELD_LABELS: dict[str, str] = {
    "product_name": "Product name",
    "manufacturer": "Manufacturer",
    "product_identifier": "Product identifier (REF / catalog / model)",
    "udi": "UDI / barcode text",
    "lot_number": "Lot / batch number",
    "expiration_date": "Expiration date",
    "manufacturing_date": "Manufacturing date",
    "safety_warnings": "Safety warnings",
    "storage_instructions": "Storage instructions",
    "sterilization": "Sterilization information",
}

# Fields that are naturally lists of statements. When comparing these, a changed
# statement is only called "modified" if it is similar enough to the original.
MULTI_VALUED: frozenset[str] = frozenset({"safety_warnings", "storage_instructions", "sterilization"})

NOT_DETECTED = "Not detected"


@dataclass(frozen=True)
class FieldHit:
    """One detected value plus the exact label text it came from."""

    value: str
    evidence: str  # the label text the value was read from
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    # How it was found: "labelled" (a keyword such as LOT or REF), "pattern" (a recognisable
    # format such as a GS1 UDI string) or "heuristic" (an inference, e.g. the largest text line).
    method: str = "labelled"


@dataclass(frozen=True)
class ExtractedField:
    key: str
    hits: tuple[FieldHit, ...] = ()

    @property
    def label(self) -> str:
        return FIELD_LABELS[self.key]

    @property
    def detected(self) -> bool:
        return bool(self.hits)

    @property
    def value(self) -> str | None:
        return self.hits[0].value if self.hits else None

    @property
    def values(self) -> list[str]:
        return [hit.value for hit in self.hits]


@dataclass(frozen=True)
class LabelFields:
    fields: dict[str, ExtractedField] = field(default_factory=dict)
    languages: tuple[LanguageGuess, ...] = ()
    # Fields whose keyword is printed (e.g. "LOT:") with no value after it. The hit's value is "".
    empty: dict[str, tuple[FieldHit, ...]] = field(default_factory=dict)

    def get(self, key: str) -> ExtractedField:
        return self.fields.get(key, ExtractedField(key))

    @property
    def detected_count(self) -> int:
        return sum(1 for item in self.fields.values() if item.detected)

    @property
    def language_summary(self) -> str:
        if not self.languages:
            return NOT_DETECTED
        return ", ".join(guess.name for guess in self.languages)

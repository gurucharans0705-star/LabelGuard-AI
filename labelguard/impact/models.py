"""Data structures for the product catalog and for impact results (no database code here)."""
from __future__ import annotations

from dataclasses import dataclass, field

from labelguard.fields.models import FIELD_LABELS

LABEL_STATUSES = ("draft", "in_review", "approved", "retired")
LABEL_STATUS_NAMES = {"draft": "Draft", "in_review": "In review", "approved": "Approved", "retired": "Retired"}
RELATIONSHIP_KINDS = ("contains_component",)  # product_id contains related_product_id
PARAMETER_KEYS = tuple(FIELD_LABELS)  # a parameter is named like the label field that shows it


@dataclass(frozen=True)
class Market:
    code: str
    name: str


@dataclass(frozen=True)
class Language:
    code: str
    name: str


@dataclass(frozen=True)
class PackagingLevel:
    code: str
    name: str
    rank: int  # 1 = the unit the user holds, higher = outer packaging


@dataclass(frozen=True)
class Product:
    product_id: str
    name: str
    category: str = ""
    source_language: str = "en"  # the language the product's text is first written in


@dataclass(frozen=True)
class ProductConfiguration:
    config_id: str
    product_id: str
    name: str
    parameters: dict[str, str] = field(default_factory=dict)  # parameter key -> current value


@dataclass(frozen=True)
class ProductRelationship:
    product_id: str  # the containing product (e.g. a kit)
    related_product_id: str  # the contained product (e.g. a component)
    kind: str = "contains_component"
    note: str = ""


@dataclass(frozen=True)
class LabelVersion:
    label_id: str
    label_code: str
    version: int
    status: str
    product_id: str
    config_id: str
    market_code: str
    language_code: str
    packaging_level_code: str
    required_fields: tuple[str, ...] = ()  # the fields this label shows
    document_sha256: str | None = None


@dataclass(frozen=True)
class LabelReviewRecord:
    record_id: str
    label_id: str
    reviewed_at: str
    reviewer: str
    outcome: str
    summary: str = ""


@dataclass(frozen=True)
class Catalog:
    """A snapshot of the product catalog. All relationships are explicit ids; nothing is inferred."""

    products: tuple[Product, ...] = ()
    configurations: tuple[ProductConfiguration, ...] = ()
    labels: tuple[LabelVersion, ...] = ()
    markets: tuple[Market, ...] = ()
    languages: tuple[Language, ...] = ()
    packaging_levels: tuple[PackagingLevel, ...] = ()
    relationships: tuple[ProductRelationship, ...] = ()
    review_records: tuple[LabelReviewRecord, ...] = ()

    def product(self, product_id: str) -> Product | None:
        return next((p for p in self.products if p.product_id == product_id), None)

    def configuration(self, config_id: str) -> ProductConfiguration | None:
        return next((c for c in self.configurations if c.config_id == config_id), None)

    def label(self, label_id: str) -> LabelVersion | None:
        return next((l for l in self.labels if l.label_id == label_id), None)

    def market_name(self, code: str) -> str:
        return next((m.name for m in self.markets if m.code == code), code)

    def language_name(self, code: str) -> str:
        return next((l.name for l in self.languages if l.code == code), code)

    def packaging_name(self, code: str) -> str:
        return next((p.name for p in self.packaging_levels if p.code == code), code)

    def labels_of_config(self, config_id: str) -> list[LabelVersion]:
        return [l for l in self.labels if l.config_id == config_id]

    def labels_of_product(self, product_id: str) -> list[LabelVersion]:
        return [l for l in self.labels if l.product_id == product_id]

    def configs_of_product(self, product_id: str) -> list[ProductConfiguration]:
        return [c for c in self.configurations if c.product_id == product_id]

    def containing_products(self, product_id: str, kinds: tuple[str, ...]) -> list[ProductRelationship]:
        """Relationships in which another product contains this one."""
        return [r for r in self.relationships if r.related_product_id == product_id and r.kind in kinds]

    def records_of_label(self, label_id: str) -> list[LabelReviewRecord]:
        return sorted((r for r in self.review_records if r.label_id == label_id), key=lambda r: r.reviewed_at, reverse=True)

    @property
    def is_empty(self) -> bool:
        return not self.products


@dataclass(frozen=True)
class ParameterChange:
    """A change to one parameter of one product configuration (what-if; the catalog is not modified)."""

    config_id: str
    parameter: str
    old_value: str | None = None
    new_value: str | None = None


@dataclass(frozen=True)
class ImpactRule:
    """A configured rule linking a changed parameter to the labels that may need review."""

    id: str
    name: str
    description: str
    parameters: tuple[str, ...]  # field keys, or ("*",) for any parameter
    scope: str  # config | product | related
    severity: str
    reason: str
    action: str
    enabled: bool = True
    label_statuses: tuple[str, ...] = ("draft", "in_review", "approved")
    packaging_levels: tuple[str, ...] = ()  # empty = every level
    markets: tuple[str, ...] = ()  # empty = every market
    require_field: bool = True  # the label must show the field to be affected
    relationship_kinds: tuple[str, ...] = ("contains_component",)
    translation_action: str = ""  # extra action for labels not in the product's source language


@dataclass(frozen=True)
class ImpactItem:
    """One label that may be affected, with the explicit relationship and rule that say why."""

    label: LabelVersion
    product_name: str
    config_name: str
    market_name: str
    language_name: str
    packaging_name: str
    rule_id: str
    rule_name: str
    severity: str
    relationship: str  # the explicit chain of records that links the change to this label
    reason: str
    actions: tuple[str, ...]

    @property
    def action_text(self) -> str:
        return " ".join(self.actions)


@dataclass(frozen=True)
class AffectedProduct:
    product_id: str
    name: str
    config_id: str | None
    config_name: str | None
    why: str


@dataclass(frozen=True)
class ExcludedLabel:
    """A label of the changed product that was checked and is NOT flagged, with the reason."""

    label: LabelVersion
    reason: str


@dataclass(frozen=True)
class ImpactResult:
    change: ParameterChange
    products: tuple[AffectedProduct, ...] = ()
    items: tuple[ImpactItem, ...] = ()
    excluded: tuple[ExcludedLabel, ...] = ()
    notes: tuple[str, ...] = ()
    rules_evaluated: int = 0

    @property
    def label_count(self) -> int:
        return len({item.label.label_id for item in self.items})

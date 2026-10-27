"""The change-impact engine: from a changed parameter to the label records that may need review.

Deterministic and local. A label is reported only when an explicit relationship in the catalog
(label -> configuration -> product, or product contains product) and an enabled, configured rule
both say so, and the reason quotes that chain. The engine never modifies the catalog and never
decides that a label is wrong or non-compliant; it says which labels a person should look at.
"""
from __future__ import annotations

from dataclasses import dataclass

from labelguard.fields.models import FIELD_LABELS
from labelguard.impact.models import (
    LABEL_STATUS_NAMES,
    PARAMETER_KEYS,
    AffectedProduct,
    Catalog,
    ExcludedLabel,
    ImpactItem,
    ImpactResult,
    ImpactRule,
    LabelVersion,
    ParameterChange,
)
from labelguard.rules.models import SEVERITIES

_RANK = {severity: position for position, severity in enumerate(SEVERITIES)}


class ImpactError(ValueError):
    """The change cannot be analysed (unknown configuration or parameter, empty catalog)."""


def _same(first: str | None, second: str | None) -> bool:
    if first is None or second is None:
        return False
    return " ".join(first.split()).casefold() == " ".join(second.split()).casefold()


def _describe(catalog: Catalog, label: LabelVersion) -> str:
    return f"{label.label_code} v{label.version} ({catalog.market_name(label.market_code)}, {catalog.language_name(label.language_code)}, {catalog.packaging_name(label.packaging_level_code)})"


def analyze_impact(catalog: Catalog, change: ParameterChange, rules: list[ImpactRule]) -> ImpactResult:
    """Find the labels that may be affected by `change`. Raises ImpactError if it cannot be analysed."""
    if catalog.is_empty:
        raise ImpactError("The product catalog is empty. Load the synthetic demo data first.")
    if change.parameter not in PARAMETER_KEYS:
        raise ImpactError(f"Unknown parameter '{change.parameter}'. Use one of: {', '.join(PARAMETER_KEYS)}.")
    config = catalog.configuration(change.config_id)
    if config is None:
        raise ImpactError(f"Unknown product configuration '{change.config_id}'.")
    product = catalog.product(config.product_id)
    if product is None:
        raise ImpactError(f"Configuration '{config.config_id}' refers to an unknown product.")

    old_value = change.old_value if change.old_value is not None else config.parameters.get(change.parameter)
    change = ParameterChange(change.config_id, change.parameter, old_value, change.new_value)
    parameter_name = FIELD_LABELS[change.parameter]
    changed_here = AffectedProduct(product.product_id, product.name, config.config_id, config.name,
                                   "The configuration whose parameter changed.")

    if _same(old_value, change.new_value):
        return ImpactResult(change, (changed_here,), notes=(
            "The old and new values are the same, so no label is affected by this change.",))

    applicable = [r for r in rules if r.enabled and ("*" in r.parameters or change.parameter in r.parameters)]
    notes: list[str] = []
    if not applicable:
        notes.append(f"No enabled impact rule reacts to '{parameter_name}', so no label was flagged. "
                     "This does not mean that no label is affected; add or enable a rule in impact_rules.toml.")

    items: list[ImpactItem] = []
    for rule in applicable:
        for label, relationship in _candidates(catalog, rule, product.product_id, config.config_id, change):
            if label.status not in rule.label_statuses:
                continue
            if rule.markets and label.market_code not in rule.markets:
                continue
            if rule.packaging_levels and label.packaging_level_code not in rule.packaging_levels:
                continue
            if rule.require_field and change.parameter not in label.required_fields:
                continue
            owner = catalog.product(label.product_id)
            label_config = catalog.configuration(label.config_id)
            actions = [rule.action]
            if rule.translation_action and owner and label.language_code != owner.source_language:
                actions.append(rule.translation_action)
            items.append(ImpactItem(
                label=label,
                product_name=owner.name if owner else label.product_id,
                config_name=label_config.name if label_config else label.config_id,
                market_name=catalog.market_name(label.market_code),
                language_name=catalog.language_name(label.language_code),
                packaging_name=catalog.packaging_name(label.packaging_level_code),
                rule_id=rule.id, rule_name=rule.name, severity=rule.severity,
                relationship=f"{relationship} It lists '{parameter_name}' among the fields it shows."
                if rule.require_field else relationship,
                reason=rule.reason, actions=tuple(actions),
            ))

    items.sort(key=lambda i: (_RANK.get(i.severity, 9), i.label.label_code, i.label.version, i.rule_id))
    flagged_ids = {i.label.label_id for i in items}
    excluded = tuple(
        ExcludedLabel(label, _exclusion_reason(label, config.config_id, change.parameter, applicable))
        for label in catalog.labels_of_product(product.product_id) if label.label_id not in flagged_ids
    )
    return ImpactResult(change, _affected_products(catalog, items, changed_here), tuple(items), excluded,
                        tuple(notes), len(applicable))


def _candidates(catalog: Catalog, rule: ImpactRule, product_id: str, config_id: str, change: ParameterChange):
    config = catalog.configuration(config_id)
    product = catalog.product(product_id)
    if rule.scope == "config":
        for label in catalog.labels_of_config(config_id):
            yield label, (f"{_describe(catalog, label)} is a label of the changed configuration "
                          f"'{config.name}' ({config.config_id}) of {product.name}.")
    elif rule.scope == "product":
        for label in catalog.labels_of_product(product_id):
            other = catalog.configuration(label.config_id)
            where = ("the changed configuration" if label.config_id == config_id
                     else f"another configuration, '{other.name}' ({other.config_id}),")
            yield label, f"{_describe(catalog, label)} is a label of {where} of the changed product {product.name}."
    else:  # related: products that contain the changed product
        for relation in catalog.containing_products(product_id, rule.relationship_kinds):
            holder = catalog.product(relation.product_id)
            for label in catalog.labels_of_product(relation.product_id):
                yield label, (f"{_describe(catalog, label)} belongs to {holder.name if holder else relation.product_id}, "
                              f"which contains {product.name} (relationship '{relation.kind}' in the catalog).")


def _exclusion_reason(label: LabelVersion, config_id: str, parameter: str, rules: list[ImpactRule]) -> str:
    if label.status == "retired":
        return f"{LABEL_STATUS_NAMES['retired']} label record; retired labels are not flagged."
    if parameter not in label.required_fields:
        return f"The label record does not list '{FIELD_LABELS[parameter]}' among the fields it shows."
    if label.config_id != config_id and not any(r.scope == "product" for r in rules):
        return "A different configuration; no enabled rule carries this parameter across configurations."
    return "No enabled rule matches this label's status, market or packaging level."


def _affected_products(catalog: Catalog, items: list[ImpactItem], changed_here: AffectedProduct) -> tuple[AffectedProduct, ...]:
    products = [changed_here]
    seen = {(changed_here.product_id, changed_here.config_id)}
    for item in items:
        key = (item.label.product_id, item.label.config_id)
        if key in seen:
            continue
        seen.add(key)
        if item.label.product_id == changed_here.product_id:
            why = "Another configuration of the same product."
        else:
            why = f"Contains {changed_here.name} (relationship recorded in the catalog)."
        products.append(AffectedProduct(item.label.product_id, item.product_name, item.label.config_id,
                                        item.config_name, why))
    return tuple(products)

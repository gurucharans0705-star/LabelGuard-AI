"""Change-impact analysis: explicit relationships plus configured rules (Day 3)."""
import dataclasses
from pathlib import Path

import pytest

from labelguard import config
from labelguard.impact.changes import changes_from_findings, suggest_config
from labelguard.impact.demo_data import demo_catalog
from labelguard.impact.engine import ImpactError, analyze_impact
from labelguard.impact.models import ParameterChange
from labelguard.impact.rules import ImpactRuleError, load_impact_rules, parse_impact_rules


@pytest.fixture(scope="module")
def catalog():
    return demo_catalog()


@pytest.fixture(scope="module")
def rules():
    return load_impact_rules()


def run(catalog, rules, config_id, parameter, new="CHANGED", old=None):
    return analyze_impact(catalog, ParameterChange(config_id, parameter, old, new), rules)


def ids(result):
    return {i.label.label_id for i in result.items}


# --- related and unrelated labels -------------------------------------------------------------------------
def test_identifier_change_hits_the_configuration_and_the_kit_that_contains_it(catalog, rules):
    result = run(catalog, rules, "C-IV200-20", "product_identifier", "NW-IV200-25")
    assert result.change.old_value == "NW-IV200-20"  # filled in from the catalog
    own = {"L-IV200-20-US-EN-U", "L-IV200-20-EU-DE-U", "L-IV200-20-EU-FR-U", "L-IV200-20-US-EN-B", "L-IV200-20-US-EN-S"}
    kit = {"L-KIT-A-US-EN-U", "L-KIT-A-EU-DE-B"}
    assert ids(result) == own | kit
    kit_item = next(i for i in result.items if i.label.label_id == "L-KIT-A-US-EN-U")
    assert kit_item.rule_id == "IMP-ID-002" and "contains" in kit_item.relationship and "contains_component" in kit_item.relationship
    assert [p.product_id for p in result.products] == ["P-IV200", "P-KIT-A"]


def test_unrelated_products_are_never_flagged(catalog, rules):
    for config_id, parameter in [("C-IV200-20", "product_identifier"), ("C-IV200-20", "safety_warnings"),
                                 ("C-IV200-20", "manufacturer"), ("C-IV200-20", "sterilization")]:
        flagged = ids(run(catalog, rules, config_id, parameter))
        assert not {i for i in flagged if "SYR50" in i or "CATH10" in i}, parameter
    reverse = ids(run(catalog, rules, "C-SYR50-STD", "product_identifier", "NW-SYR50-B"))
    assert reverse == {"L-SYR50-US-EN-U", "L-SYR50-CA-FR-U"}  # nothing contains the syringe


def test_a_sibling_configuration_is_not_flagged_by_a_configuration_scoped_rule(catalog, rules):
    result = run(catalog, rules, "C-IV200-20", "product_identifier")
    assert not any("IV200-15" in i for i in ids(result))
    reasons = {e.label.label_id: e.reason for e in result.excluded}
    assert "different configuration" in reasons["L-IV200-15-US-EN-U"].lower()


def test_a_safety_change_crosses_configurations_and_adds_a_translation_action(catalog, rules):
    result = run(catalog, rules, "C-IV200-20", "safety_warnings", "Single use only.")
    flagged = ids(result)
    assert {"L-IV200-15-US-EN-U", "L-IV200-15-EU-DE-U", "L-KIT-A-US-EN-U"} <= flagged
    german = next(i for i in result.items if i.label.label_id == "L-IV200-20-EU-DE-U")
    english = next(i for i in result.items if i.label.label_id == "L-IV200-20-US-EN-U")
    assert german.severity == "critical_review" and "translated warning" in german.action_text
    assert "translated" not in english.action_text
    assert result.items[0].severity == "critical_review"  # most urgent first


def test_every_impact_has_a_reason_actions_markets_and_languages(catalog, rules):
    result = run(catalog, rules, "C-IV200-20", "safety_warnings")
    for item in result.items:
        assert item.reason and item.relationship and item.actions and item.rule_id.startswith("IMP-")
        assert item.market_name in ("United States", "European Union") and item.language_name and item.packaging_name


def test_retired_labels_are_left_out_with_a_reason(catalog, rules):
    result = run(catalog, rules, "C-IV200-20", "product_identifier")
    assert "L-IV200-20-JP-JA-U" not in ids(result)
    excluded = {e.label.label_id: e.reason for e in result.excluded}
    assert "Retired" in excluded["L-IV200-20-JP-JA-U"]


def test_packaging_filter_and_required_field_filter(catalog, rules):
    sterilization = run(catalog, rules, "C-IV200-20", "sterilization")
    assert "L-IV200-20-US-EN-S" not in ids(sterilization)  # shippers are outside IMP-STER-001
    assert "L-IV200-20-US-EN-U" in ids(sterilization)
    shipper_reason = next(e.reason for e in sterilization.excluded if e.label.label_id == "L-IV200-20-US-EN-S")
    assert "does not list" in shipper_reason or "packaging" in shipper_reason


def test_label_must_show_the_field_unless_the_rule_says_otherwise(catalog, rules):
    stripped = dataclasses.replace(catalog.label("L-IV200-20-US-EN-U"), required_fields=("product_name",))
    small = dataclasses.replace(catalog, labels=tuple(stripped if l.label_id == stripped.label_id else l for l in catalog.labels))
    assert "L-IV200-20-US-EN-U" not in ids(run(small, rules, "C-IV200-20", "product_identifier"))
    loose = [dataclasses.replace(r, require_field=False) for r in rules]
    assert "L-IV200-20-US-EN-U" in ids(run(small, loose, "C-IV200-20", "product_identifier"))


def test_market_filter(catalog, rules):
    only_eu = [dataclasses.replace(r, markets=("EU",)) for r in rules]
    result = run(catalog, only_eu, "C-IV200-20", "product_identifier")
    assert ids(result) and all(i.label.market_code == "EU" for i in result.items)


# --- edge cases and errors -----------------------------------------------------------------------------------
def test_no_change_means_no_impact(catalog, rules):
    result = run(catalog, rules, "C-IV200-20", "product_identifier", "  nw-iv200-20 ")
    assert result.items == () and "same" in result.notes[0]


def test_parameter_without_a_rule_says_so_instead_of_claiming_nothing_is_affected(catalog, rules):
    result = analyze_impact(catalog, ParameterChange("C-IV200-20", "udi", None, "(01)00000000000000"), [r for r in rules if r.id == "IMP-STOR-001"])
    assert result.items == () and "does not mean that no label is affected" in result.notes[0]


def test_disabled_rules_do_nothing(catalog, rules):
    off = [dataclasses.replace(r, enabled=False) for r in rules]
    assert run(catalog, off, "C-IV200-20", "product_identifier").items == ()


def test_catalog_is_not_modified_by_an_analysis(catalog, rules):
    before = catalog.configuration("C-IV200-20").parameters["product_identifier"]
    run(catalog, rules, "C-IV200-20", "product_identifier", "ZZZ")
    assert catalog.configuration("C-IV200-20").parameters["product_identifier"] == before


@pytest.mark.parametrize("config_id, parameter, message", [
    ("C-NOPE", "product_identifier", "Unknown product configuration"),
    ("C-IV200-20", "colour", "Unknown parameter"),
])
def test_unknown_inputs_raise_a_clear_error(catalog, rules, config_id, parameter, message):
    with pytest.raises(ImpactError, match=message):
        run(catalog, rules, config_id, parameter)


def test_empty_catalog_raises(rules):
    from labelguard.impact.models import Catalog

    with pytest.raises(ImpactError, match="empty"):
        run(Catalog(), rules, "C-IV200-20", "product_identifier")


# --- rules file ----------------------------------------------------------------------------------------------------
def test_shipped_rules_load_and_the_fallback_rule_is_disabled(rules):
    assert len(rules) == 10 and {r.id for r in rules if not r.enabled} == {"IMP-ANY-001"}
    assert all(r.scope in ("config", "product", "related") for r in rules)


@pytest.mark.parametrize("text, fragment", [
    ("not = [valid", "not valid TOML"),
    ("title = 'x'", r"No \[\[rule\]\]"),
    ("[[rule]]\nid='A'\nname='n'\ndescription='d'\nparameters=['nope']\nscope='config'\nseverity='high'\nreason='r'\naction='a'\n", "nope"),
    ("[[rule]]\nid='A'\nname='n'\ndescription='d'\nparameters=['udi']\nscope='galaxy'\nseverity='high'\nreason='r'\naction='a'\n", "scope"),
    ("[[rule]]\nid='A'\nname='n'\ndescription='d'\nparameters=['udi']\nscope='config'\nseverity='huge'\nreason='r'\naction='a'\n", "severity"),
    ("[[rule]]\nid='A'\nname='n'\ndescription='d'\nparameters=['udi']\nscope='config'\nseverity='high'\nreason='r'\naction='a'\nbogus=1\n", "bogus"),
])
def test_a_broken_rules_file_is_reported_with_the_problem(tmp_path, text, fragment):
    path = tmp_path / "bad.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ImpactRuleError, match=fragment):
        load_impact_rules(path)


def test_missing_rules_file(tmp_path):
    with pytest.raises(ImpactRuleError, match="not found"):
        load_impact_rules(tmp_path / "nope.toml")


def test_duplicate_rule_ids_are_rejected():
    entry = {"id": "A", "name": "n", "description": "d", "parameters": ["udi"], "scope": "config",
             "severity": "high", "reason": "r", "action": "a"}
    with pytest.raises(ImpactRuleError, match="A"):
        parse_impact_rules({"rule": [entry, dict(entry)]})


# --- from a real comparison ---------------------------------------------------------------------------------------
def test_findings_become_parameter_changes(sample_verification):
    changes = {c.parameter: c for c in changes_from_findings(sample_verification.report.findings)}
    assert changes["product_identifier"].old_value == "NW-IV200-20" and changes["product_identifier"].new_value == "NW-IV200-15"
    assert changes["product_identifier"].finding_ids


def test_a_configuration_is_suggested_from_recorded_old_values(sample_verification, catalog):
    changes = changes_from_findings(sample_verification.report.findings)
    assert suggest_config(catalog, changes) == "C-IV200-20"
    assert suggest_config(catalog, changes, label_id="L-SYR50-US-EN-U") == "C-SYR50-STD"  # a linked label wins
    assert suggest_config(catalog, []) is None


def test_the_end_to_end_flow_through_the_workflow_layer_saves_and_audits(sample_verification):
    from labelguard import workflow
    from labelguard.db import audit, catalog as store

    store.load_demo_data()
    result, run_uid = workflow.run_impact_analysis(
        store.load_catalog(), ParameterChange("C-IV200-20", "product_identifier", None, "NW-IV200-15"))
    assert run_uid and result.label_count == 7
    (saved,) = store.list_impact_runs()
    assert saved.run_uid == run_uid and saved.item_count == len(result.items) and saved.is_demo
    assert len(store.load_impact_items(run_uid)) == len(result.items)
    assert [e.entity_id for e in audit.list_events(event_types=[audit.IMPACT_ANALYSIS_RUN])] == [run_uid]

import pytest

from labelguard import config
from labelguard.rules.loader import RuleConfigError, load_rules, parse_rules
from labelguard.validation.identifiers import compare_dates, gtin_check_digit, gtin_is_valid, extract_gtin, parse_label_date

GOOD = {"rule": [{"id": "R-1", "name": "n", "description": "d", "type": "page_count_changed",
                  "severity": "low", "action": "look"}]}


def rule(**changes):
    entry = dict(GOOD["rule"][0])
    entry.update(changes)
    return {"rule": [entry]}


def test_shipped_rules_file_loads_and_is_consistent():
    rules = load_rules()
    ids = [r.id for r in rules]
    assert len(ids) == len(set(ids)) and len(rules) >= 30
    assert all(r.action for r in rules)  # every rule tells the reviewer what to do


def test_valid_rule_parses():
    (parsed,) = parse_rules(GOOD)
    assert parsed.severity == "low" and parsed.enabled


@pytest.mark.parametrize("data, fragment", [
    (rule(severity="catastrophic"), "severity"),
    (rule(severty="low"), "severty"),  # misspelled key must be an error, not ignored
    (rule(type="made_up"), "type"),
    (rule(id=""), "id"),
])
def test_invalid_rules_are_rejected_with_a_readable_reason(data, fragment):
    with pytest.raises(RuleConfigError) as error:
        parse_rules(data)
    assert fragment in str(error.value)


def test_duplicate_ids_are_rejected():
    with pytest.raises(RuleConfigError, match="R-1"):
        parse_rules({"rule": GOOD["rule"] * 2})


def test_all_problems_are_listed_together():
    bad = {"rule": [dict(GOOD["rule"][0], severity="x"), dict(GOOD["rule"][0], id="R-2", type="nope")]}
    with pytest.raises(RuleConfigError) as error:
        parse_rules(bad)
    assert len(error.value.errors) >= 2


def test_missing_and_malformed_files(tmp_path):
    with pytest.raises(RuleConfigError, match="not found"):
        load_rules(tmp_path / "absent.toml")
    broken = tmp_path / "bad.toml"
    broken.write_text("[[rule]\nid = ", encoding="utf-8")
    with pytest.raises(RuleConfigError, match="TOML"):
        load_rules(broken)


def test_rules_path_follows_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RULES_PATH", tmp_path / "nope.toml")
    with pytest.raises(RuleConfigError):
        load_rules()


def test_rules_and_engine_use_no_llm_or_network_library():
    """Deterministic results must not depend on an LLM or the network (Day 4's AI step comes after)."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "labelguard"
    banned = re.compile(r"^\s*(import|from)\s+(anthropic|openai|requests|httpx|urllib\.request|socket)\b", re.M)
    for folder in ("rules", "comparison", "validation", "vision"):
        for path in (root / folder).rglob("*.py"):
            assert not banned.search(path.read_text(encoding="utf-8")), path
    assert not banned.search((root / "verification.py").read_text(encoding="utf-8"))


# --- identifiers -------------------------------------------------------------------------------------
def test_gtin_check_digit():
    assert gtin_check_digit("0890123456789") == 0
    assert gtin_is_valid("08901234567890") is True
    assert gtin_is_valid("08901234567899") is False
    assert gtin_is_valid("12345") is None and gtin_is_valid("abc") is None


def test_extract_gtin():
    assert extract_gtin("(01)08901234567890(17)290630") == "08901234567890"
    assert extract_gtin("08901234567890") == "08901234567890"
    assert extract_gtin("no gtin here") is None


def test_dates_parse_and_compare():
    assert parse_label_date("2029-06-30") is not None
    assert parse_label_date("30 JUN 2029") is not None
    assert parse_label_date("not a date") is None
    assert parse_label_date("04/05/2029") is None  # day/month order is ambiguous, so it is not guessed
    before, after = parse_label_date("2023-01-01"), parse_label_date("2024-07-01")
    assert compare_dates(before, after) == -1 and compare_dates(after, before) == 1
    assert compare_dates(before, before) == 0

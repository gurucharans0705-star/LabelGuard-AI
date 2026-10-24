"""Load and validate the rules file (config/rules.toml).

Validation is strict on purpose: a misspelled setting such as "severty" must be an error the
user can see, not a rule that silently behaves differently from what the file says.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from labelguard import config
from labelguard.fields.models import FIELD_LABELS
from labelguard.rules.models import CATEGORIES, SEVERITIES, Rule

CHANGE_KINDS = ("modified", "added", "removed")
SIDES = ("both", "approved", "revised")
_COMMON_KEYS = {"id", "name", "description", "type", "severity", "category", "action", "enabled"}
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")

# type -> (required settings, optional settings, default category or None if it depends on the change kind)
_TYPES: dict[str, tuple[set[str], set[str], str | None]] = {
    "required_field": ({"field"}, {"applies_to"}, "missing_expected_field"),
    "field_not_empty": ({"fields"}, {"applies_to"}, "missing_expected_field"),
    "field_change": ({"field"}, {"change_kinds"}, None),
    "text_change": ({"kind"}, set(), None),
    "page_count_changed": (set(), set(), "modified_content"),
    "udi_check_digit": (set(), {"applies_to"}, "identifier_mismatch"),
    "date_order": (set(), set(), "consistency_issue"),
    "barcode_text_match": (set(), {"applies_to"}, "identifier_mismatch"),
    "barcode_changed": (set(), set(), "identifier_mismatch"),
    "visual_difference": (set(), set(), "potential_artwork_discrepancy"),
}
RULE_TYPES: tuple[str, ...] = tuple(_TYPES)


class RuleConfigError(Exception):
    """The rules file is missing or invalid. `errors` lists every problem found."""

    def __init__(self, source: str, errors: list[str]):
        self.source = source
        self.errors = errors
        bullet = "\n".join(f"- {error}" for error in errors)
        super().__init__(f"The rules file ({source}) has {len(errors)} problem(s):\n{bullet}")


def load_rules(path: Path | str | None = None) -> list[Rule]:
    """Read the rules file. Raises RuleConfigError with a readable message if it cannot be used."""
    source = Path(path) if path is not None else config.RULES_PATH
    try:
        raw = source.read_bytes()
    except FileNotFoundError:
        raise RuleConfigError(str(source), ["The file was not found."]) from None
    except OSError as exc:
        raise RuleConfigError(str(source), [f"The file could not be read ({exc.__class__.__name__})."]) from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise RuleConfigError(str(source), ["The file is not valid UTF-8 text."]) from exc
    except tomllib.TOMLDecodeError as exc:
        raise RuleConfigError(str(source), [f"The file is not valid TOML: {exc}"]) from exc
    return parse_rules(data, str(source))


def parse_rules(data: dict[str, Any], source: str = "rules") -> list[Rule]:
    """Validate parsed TOML data and build Rule objects."""
    entries = data.get("rule")
    if not isinstance(entries, list) or not entries:
        raise RuleConfigError(source, ["No rules found. Define each rule as a [[rule]] table."])

    errors: list[str] = []
    rules: list[Rule] = []
    seen: set[str] = set()
    for number, entry in enumerate(entries, start=1):
        label = f"rule #{number}"
        if not isinstance(entry, dict):
            errors.append(f"{label}: must be a table.")
            continue
        label = f"rule '{entry.get('id', number)}'"
        rule = _build_rule(entry, label, errors)
        if rule is None:
            continue
        if rule.id in seen:
            errors.append(f"{label}: the id is used more than once.")
            continue
        seen.add(rule.id)
        rules.append(rule)
    if errors:
        raise RuleConfigError(source, errors)
    return rules


def _build_rule(entry: dict[str, Any], label: str, errors: list[str]) -> Rule | None:
    start = len(errors)

    def text(key: str, required: bool = True) -> str:
        value = entry.get(key, "")
        if not isinstance(value, str) or (required and not value.strip()):
            errors.append(f"{label}: '{key}' must be a non-empty text value." if required else f"{label}: '{key}' must be text.")
            return ""
        return value.strip()

    rule_id, name, description, action = text("id"), text("name"), text("description", False), text("action", False)
    if rule_id and not _ID_RE.match(rule_id):
        errors.append(f"{label}: the id may only contain letters, digits, '-', '_' and '.'.")

    rule_type = text("type")
    if rule_type and rule_type not in _TYPES:
        errors.append(f"{label}: unknown type '{rule_type}'. Known types: {', '.join(RULE_TYPES)}.")
        return None

    severity = text("severity")
    if severity and severity not in SEVERITIES:
        errors.append(f"{label}: unknown severity '{severity}'. Use one of: {', '.join(SEVERITIES)}.")

    enabled = entry.get("enabled", True)
    if not isinstance(enabled, bool):
        errors.append(f"{label}: 'enabled' must be true or false.")

    if not rule_type:
        return None
    required, optional, default_category = _TYPES[rule_type]
    category = entry.get("category")
    if category is not None and category not in CATEGORIES:
        errors.append(f"{label}: unknown category '{category}'. Use one of: {', '.join(CATEGORIES)}.")
    if category is None and default_category is not None:
        category = default_category

    unknown = set(entry) - _COMMON_KEYS - required - optional
    for key in sorted(unknown):
        errors.append(f"{label}: unknown setting '{key}' for type '{rule_type}'.")
    for key in sorted(required - set(entry)):
        errors.append(f"{label}: type '{rule_type}' requires the setting '{key}'.")

    params = {key: entry[key] for key in (required | optional) if key in entry}
    _check_params(params, label, errors)
    if "applies_to" in optional:
        params.setdefault("applies_to", "both")
    if rule_type == "field_change":
        params.setdefault("change_kinds", list(CHANGE_KINDS))
    if len(errors) > start:
        return None
    return Rule(rule_id, name, description, rule_type, severity, category, action, enabled, params)


def _check_params(params: dict[str, Any], label: str, errors: list[str]) -> None:
    if "field" in params and params["field"] not in FIELD_LABELS:
        errors.append(f"{label}: unknown field '{params['field']}'. Known fields: {', '.join(FIELD_LABELS)}.")
    if "fields" in params:
        fields = params["fields"]
        if not isinstance(fields, list) or not fields or not all(isinstance(item, str) for item in fields):
            errors.append(f"{label}: 'fields' must be a non-empty list of field names.")
        else:
            for item in fields:
                if item not in FIELD_LABELS:
                    errors.append(f"{label}: unknown field '{item}'. Known fields: {', '.join(FIELD_LABELS)}.")
    if "applies_to" in params and params["applies_to"] not in SIDES:
        errors.append(f"{label}: 'applies_to' must be one of: {', '.join(SIDES)}.")
    if "change_kinds" in params:
        kinds = params["change_kinds"]
        if not isinstance(kinds, list) or not kinds or any(kind not in CHANGE_KINDS for kind in kinds):
            errors.append(f"{label}: 'change_kinds' must be a non-empty list using only: {', '.join(CHANGE_KINDS)}.")
    if "kind" in params and params["kind"] not in ("added", "removed", "modified"):
        errors.append(f"{label}: 'kind' must be one of: added, removed, modified.")

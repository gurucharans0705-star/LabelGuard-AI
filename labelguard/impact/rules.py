"""Load and validate config/impact_rules.toml (strict: a mistake is an error, never silently ignored)."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from labelguard import config
from labelguard.impact.models import LABEL_STATUSES, PARAMETER_KEYS, RELATIONSHIP_KINDS, ImpactRule
from labelguard.rules.models import SEVERITIES

SCOPES = ("config", "product", "related")
PACKAGING = ("unit", "box", "shipper")
_KEYS = {
    "id", "name", "description", "parameters", "scope", "severity", "reason", "action", "enabled",
    "label_statuses", "packaging_levels", "markets", "require_field", "relationship_kinds", "translation_action",
}
_REQUIRED = ("id", "name", "parameters", "scope", "severity", "reason", "action")


class ImpactRuleError(Exception):
    """The impact rules file is missing or invalid. `errors` lists every problem found."""

    def __init__(self, source: str, errors: list[str]):
        self.source = source
        self.errors = errors
        bullet = "\n".join(f"- {error}" for error in errors)
        super().__init__(f"The impact rules file ({source}) has {len(errors)} problem(s):\n{bullet}")


def load_impact_rules(path: Path | str | None = None) -> list[ImpactRule]:
    source = Path(path) if path is not None else config.IMPACT_RULES_PATH
    try:
        raw = source.read_bytes()
    except FileNotFoundError:
        raise ImpactRuleError(str(source), ["The file was not found."]) from None
    except OSError as exc:
        raise ImpactRuleError(str(source), [f"The file could not be read ({exc.__class__.__name__})."]) from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ImpactRuleError(str(source), ["The file is not valid UTF-8 text."]) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ImpactRuleError(str(source), [f"The file is not valid TOML: {exc}"]) from exc
    return parse_impact_rules(data, str(source))


def _strings(entry: dict[str, Any], key: str, label: str, errors: list[str], allowed: tuple[str, ...] | None,
             default: tuple[str, ...]) -> tuple[str, ...]:
    if key not in entry:
        return default
    value = entry[key]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        errors.append(f"{label}: '{key}' must be a list of text values.")
        return default
    if allowed is not None:
        for item in value:
            if item not in allowed:
                errors.append(f"{label}: unknown value '{item}' in '{key}'. Use: {', '.join(allowed)}.")
    return tuple(value)


def parse_impact_rules(data: dict[str, Any], source: str = "impact rules") -> list[ImpactRule]:
    entries = data.get("rule")
    errors: list[str] = []
    if not isinstance(entries, list) or not entries:
        raise ImpactRuleError(source, ["No [[rule]] entries were found."])
    for key in sorted(set(data) - {"rule"}):
        errors.append(f"Unknown top-level setting '{key}'.")
    rules: list[ImpactRule] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            errors.append(f"Rule #{index}: must be a table.")
            continue
        rule_id = entry.get("id") if isinstance(entry.get("id"), str) else ""
        label = f"Rule {rule_id}" if rule_id else f"Rule #{index}"
        start = len(errors)
        for key in sorted(set(entry) - _KEYS):
            errors.append(f"{label}: unknown setting '{key}'.")
        for key in _REQUIRED:
            if key not in entry:
                errors.append(f"{label}: missing required setting '{key}'.")
            elif key != "parameters" and (not isinstance(entry[key], str) or not entry[key].strip()):
                errors.append(f"{label}: '{key}' must be a non-empty text value.")
        if rule_id in seen:
            errors.append(f"{label}: the id is used more than once.")
        seen.add(rule_id)
        if entry.get("severity") not in SEVERITIES and isinstance(entry.get("severity"), str):
            errors.append(f"{label}: unknown severity '{entry['severity']}'. Use one of: {', '.join(SEVERITIES)}.")
        if isinstance(entry.get("scope"), str) and entry["scope"] not in SCOPES:
            errors.append(f"{label}: unknown scope '{entry['scope']}'. Use one of: {', '.join(SCOPES)}.")
        enabled, require_field = entry.get("enabled", True), entry.get("require_field", True)
        for key, value in (("enabled", enabled), ("require_field", require_field)):
            if not isinstance(value, bool):
                errors.append(f"{label}: '{key}' must be true or false.")
        parameters = _strings(entry, "parameters", label, errors, PARAMETER_KEYS + ("*",), ())
        if "parameters" in entry and not parameters:
            errors.append(f"{label}: 'parameters' must not be empty.")
        statuses = _strings(entry, "label_statuses", label, errors, LABEL_STATUSES, ("draft", "in_review", "approved"))
        levels = _strings(entry, "packaging_levels", label, errors, PACKAGING, ())
        markets = _strings(entry, "markets", label, errors, None, ())
        kinds = _strings(entry, "relationship_kinds", label, errors, RELATIONSHIP_KINDS, ("contains_component",))
        if len(errors) > start:
            continue
        rules.append(ImpactRule(
            id=rule_id, name=entry["name"].strip(), description=str(entry.get("description", "")).strip(),
            parameters=parameters, scope=entry["scope"], severity=entry["severity"], reason=entry["reason"].strip(),
            action=entry["action"].strip(), enabled=enabled, label_statuses=statuses, packaging_levels=levels,
            markets=markets, require_field=require_field, relationship_kinds=kinds,
            translation_action=str(entry.get("translation_action", "")).strip(),
        ))
    if errors:
        raise ImpactRuleError(source, errors)
    return rules

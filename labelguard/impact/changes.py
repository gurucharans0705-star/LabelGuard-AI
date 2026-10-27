"""Turn the findings of a label comparison into parameter changes for impact analysis."""
from __future__ import annotations

from dataclasses import dataclass

from labelguard.impact.models import PARAMETER_KEYS
from labelguard.rules.models import RESULT_FLAGGED, Finding

_MAX = 400


@dataclass(frozen=True)
class DetectedChange:
    """A label field whose value differs between the approved and revised label."""

    parameter: str
    old_value: str | None
    new_value: str | None
    finding_ids: tuple[str, ...]


def _join(values: list[str]) -> str | None:
    unique = list(dict.fromkeys(v for v in values if v))
    if not unique:
        return None
    text = "; ".join(unique)
    return text if len(text) <= _MAX else text[: _MAX - 1] + "…"


def changes_from_findings(findings: tuple[Finding, ...] | list[Finding]) -> list[DetectedChange]:
    """One change per field that has a flagged finding with a differing value (several findings on one field are merged)."""
    grouped: dict[str, list[Finding]] = {}
    for finding in findings:
        if finding.result == RESULT_FLAGGED and finding.field in PARAMETER_KEYS and finding.approved_value != finding.revised_value:
            grouped.setdefault(finding.field, []).append(finding)
    return [
        DetectedChange(
            parameter=key,
            old_value=_join([f.approved_value or "" for f in group]),
            new_value=_join([f.revised_value or "" for f in group]),
            finding_ids=tuple(f.id for f in group),
        )
        for key, group in grouped.items()
    ]


def suggest_config(catalog, changes: list[DetectedChange], label_id: str | None = None) -> str | None:
    """The catalog configuration a detected change most likely belongs to, or None if nothing matches.

    A configuration matches when the OLD value of a changed field equals what the catalog records for that
    configuration, so the suggestion rests on recorded values, not on names. A label record that the analysis was
    linked to wins. This is only a default for the person to confirm.
    """
    if label_id:
        label = catalog.label(label_id)
        if label is not None:
            return label.config_id
    scores: dict[str, int] = {}
    for change in changes:
        if not change.old_value:
            continue
        wanted = " ".join(change.old_value.split()).casefold()
        for config in catalog.configurations:
            recorded = config.parameters.get(change.parameter)
            if recorded and " ".join(recorded.split()).casefold() == wanted:
                scores[config.config_id] = scores.get(config.config_id, 0) + 1
    return max(scores, key=lambda key: (scores[key], key)) if scores else None

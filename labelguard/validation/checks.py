"""Completeness checks on extracted label fields.

These are *observations for a human reviewer*, not regulatory determinations.
Which elements a given label must carry depends on the device, market and
applicable rules, which this software does not know. The lists below are a
generic starting checklist that Day 2 will make configurable.
"""
from __future__ import annotations

from dataclasses import dataclass

from labelguard.fields.models import FIELD_LABELS, LabelFields

# Elements most medical device labels are expected to show. Not detecting one means
# "check manually" (it may be absent, worded unusually, or unreadable).
EXPECTED_FIELDS: tuple[str, ...] = (
    "product_name",
    "manufacturer",
    "product_identifier",
    "udi",
    "lot_number",
    "expiration_date",
    "safety_warnings",
)
# Elements that only apply to some devices, so a miss is informational.
CONTEXTUAL_FIELDS: tuple[str, ...] = ("manufacturing_date", "storage_instructions", "sterilization")


@dataclass(frozen=True)
class Observation:
    level: str  # "review" (please check) or "info" (may not apply)
    field: str
    message: str


def check_label(fields: LabelFields) -> tuple[Observation, ...]:
    """List expected elements that were not detected on a label."""
    observations: list[Observation] = []
    for key in EXPECTED_FIELDS:
        if not fields.get(key).detected:
            observations.append(Observation(
                "review",
                key,
                f"{FIELD_LABELS[key]} was not detected. Check the source label: it may be absent, "
                "worded unusually, or unreadable.",
            ))
    for key in CONTEXTUAL_FIELDS:
        if not fields.get(key).detected:
            observations.append(Observation(
                "info",
                key,
                f"{FIELD_LABELS[key]} was not detected. This may not apply to this device.",
            ))
    return tuple(observations)

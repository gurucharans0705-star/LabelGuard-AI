"""Data structures for explanations, and the errors the AI layer can raise."""
from __future__ import annotations

from dataclasses import dataclass, field

AI_ASSISTED = "ai"
DETERMINISTIC = "deterministic"
AI_LABEL = "AI-assisted"
DETERMINISTIC_LABEL = "Deterministic (no AI)"


class AiError(Exception):
    """Base class: the AI explanation could not be produced. The caller falls back to the deterministic one."""


class AiUnavailable(AiError):
    """No key, switched off, or the SDK is not installed."""


class AiTimeout(AiError):
    """The API did not answer in time."""


class AiRequestError(AiError):
    """The API refused or failed (authentication, rate limit, server error, network)."""


class AiResponseError(AiError):
    """The model's answer was malformed, ungrounded or broke a safety rule. `reasons` lists why."""

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons))


@dataclass(frozen=True)
class EvidenceRef:
    """A piece of evidence the explanation may cite. `ref` is what the model must quote back."""

    ref: str  # e.g. "R-ID-001-da8edb8e#E1"
    source: str  # approved | revised | both
    page: int | None
    text: str


@dataclass(frozen=True)
class FindingInput:
    """One finding as given to the explainer (everything the model is allowed to know about it)."""

    finding_id: str
    rule_id: str
    rule_name: str
    rule_description: str
    severity: str
    category: str
    result: str
    title: str
    description: str
    approved_value: str | None
    revised_value: str | None
    recommended_action: str
    evidence: tuple[EvidenceRef, ...]

    @property
    def refs(self) -> tuple[str, ...]:
        return tuple(e.ref for e in self.evidence)


@dataclass(frozen=True)
class ExplanationItem:
    finding_id: str
    text: str
    evidence_refs: tuple[str, ...]
    generated_by: str  # AI_ASSISTED | DETERMINISTIC


@dataclass(frozen=True)
class Explanation:
    """What the page shows. `source` says whether any of it came from the model."""

    source: str  # AI_ASSISTED | DETERMINISTIC
    summary: str
    items: tuple[ExplanationItem, ...]
    questions: tuple[str, ...]
    model: str = ""
    provider: str = ""
    note: str = ""  # e.g. why the fallback was used
    finding_ids: tuple[str, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        return AI_LABEL if self.source == AI_ASSISTED else DETERMINISTIC_LABEL

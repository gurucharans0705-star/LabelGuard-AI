"""Produce an explanation: AI-assisted when configured and trustworthy, deterministic otherwise.

Nothing here changes a finding. The model's answer is shown only after validation; on any failure
(no key, SDK missing, timeout, API error, malformed or ungrounded answer) the deterministic
explanation is returned instead, with a note saying why.
"""
from __future__ import annotations

import logging
from typing import Sequence

from labelguard.ai import fallback, prompt, validate
from labelguard.ai.config import AiSettings, get_settings
from labelguard.ai.models import (
    AI_ASSISTED,
    DETERMINISTIC,
    AiError,
    AiResponseError,
    Explanation,
    ExplanationItem,
    FindingInput,
)
from labelguard.ai.provider import ClaudeProvider, Provider
from labelguard.rules.models import Finding, Rule

logger = logging.getLogger(__name__)

MODE_DETERMINISTIC = "deterministic"
MODE_AI = "ai"


def explain(
    findings: Sequence[Finding],
    rules: Sequence[Rule],
    *,
    mode: str = MODE_DETERMINISTIC,
    provider: Provider | None = None,
    settings: AiSettings | None = None,
) -> Explanation:
    """Explain `findings`. `mode="ai"` tries the model; anything else (or any failure) is deterministic."""
    inputs, omitted = prompt.build_inputs(findings, rules)
    if mode != MODE_AI or not inputs:
        return fallback.deterministic_explanation(inputs, omitted)

    settings = settings or get_settings()
    provider = provider or ClaudeProvider(settings)
    try:
        raw = provider.complete(prompt.SYSTEM_PROMPT, prompt.user_message(inputs))
        parsed = validate.validate_response(raw, inputs)
    except AiResponseError as exc:
        logger.warning("AI response rejected: %d reason(s)", len(exc.reasons))
        return fallback.deterministic_explanation(inputs, omitted, note=(
            "The AI response was rejected by validation, so the deterministic explanation is shown. Reasons: "
            + "; ".join(exc.reasons[:5]) + ("; ..." if len(exc.reasons) > 5 else "")))
    except AiError as exc:
        return fallback.deterministic_explanation(inputs, omitted, note=(
            f"AI explanation unavailable: {exc} The deterministic explanation is shown instead."))
    return _merge(inputs, parsed, omitted, provider, settings)


def _merge(inputs: list[FindingInput], parsed: validate.ParsedResponse, omitted: int, provider: Provider,
           settings: AiSettings) -> Explanation:
    """The validated AI text, plus a deterministic item for any finding the model did not explain."""
    explained = {item.finding_id: item for item in parsed.items}
    items: list[ExplanationItem] = []
    missing = 0
    for finding in inputs:
        item = explained.get(finding.finding_id)
        if item is not None:
            items.append(ExplanationItem(finding.finding_id, item.text, item.evidence_refs, AI_ASSISTED))
        else:
            items.append(fallback.explain_finding(finding))
            missing += 1
    note = f"{missing} finding(s) were not explained by the model; a deterministic explanation is shown for them." if missing else ""
    if omitted:
        note = (note + " " if note else "") + f"Only the {len(inputs)} most severe findings were sent; {omitted} more were not."
    return Explanation(
        source=AI_ASSISTED, summary=parsed.summary, items=tuple(items),
        questions=parsed.questions or fallback.build_questions(inputs),
        model=getattr(provider, "model", settings.model), provider=getattr(provider, "name", "claude"),
        note=note, finding_ids=tuple(f.finding_id for f in inputs),
    )
